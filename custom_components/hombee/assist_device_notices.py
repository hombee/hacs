"""Route a failed Assist attempt to its satellite's local audio."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import StrEnum
from weakref import WeakKeyDictionary

from homeassistant.const import EVENT_STATE_CHANGED, EVENT_STATE_REPORTED
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

_LOGGER = logging.getLogger(__name__)


class VoiceNotice(StrEnum):
    """Recording names shared with the Hombee Voice firmware."""

    ACCESS = "access"
    ALLOWANCE = "allowance"
    RETRY = "retry"
    UNCERTAIN = "uncertain"


def before_action_notice(code: str) -> VoiceNotice:
    """Classify a failure only when this attempt could not have run actions."""
    if code in {"subscription_inactive", "not_associated"}:
        return VoiceNotice.ACCESS
    if code == "budget_exhausted":
        return VoiceNotice.ALLOWANCE
    return VoiceNotice.RETRY


@dataclass(frozen=True, slots=True)
class NoticeTarget:
    service: str
    language: str


class DeviceNotices:
    """Use the satellite state emitted inline by Assist before calling STT."""

    def __init__(self, hass, entry) -> None:
        self.hass = hass
        self.entry = entry
        self._listening = WeakKeyDictionary()

    def async_start(self) -> None:
        """Track state transitions as well as repeated writes of listening."""
        for event_type in (EVENT_STATE_CHANGED, EVENT_STATE_REPORTED):
            self.entry.async_on_unload(
                self.hass.bus.async_listen(
                    event_type,
                    self._async_listening,
                    event_filter=self._is_listening,
                )
            )

    @staticmethod
    @callback
    def _is_listening(data) -> bool:
        state = data.get("new_state")
        return (
            data["entity_id"].startswith("assist_satellite.")
            and state is not None
            and state.state == "listening"
        )

    @callback
    def _async_listening(self, event) -> None:
        # Home Assistant calls the listening callback and STT in the same task.
        # A task key keeps simultaneous attempts separate and cannot go stale.
        if task := asyncio.current_task():
            self._listening[task] = event.data["entity_id"]

    def target_for_stt(self, language: str) -> NoticeTarget | None:
        task = asyncio.current_task()
        satellite = self._listening.pop(task, None) if task is not None else None
        return self.target_for_satellite(satellite, language)

    def target_for_satellite(
        self, satellite: str | None, language: str
    ) -> NoticeTarget | None:
        language = language.lower().replace("_", "-").split("-", 1)[0]
        if satellite is None or language not in {"en", "pl"}:
            return None
        entity = er.async_get(self.hass).async_get(satellite)
        if entity is None or entity.platform != "esphome" or not entity.config_entry_id:
            return None
        config = self.hass.config_entries.async_get_entry(entity.config_entry_id)
        if config is None or not (name := config.data.get("device_name")):
            return None
        # ESPHome names device actions after the node, not its editable HA name.
        service = f"{name.replace('-', '_')}_play_hombee_notice"
        if not self.hass.services.has_service("esphome", service):
            return None
        return NoticeTarget(service, language)

    def play(self, target: NoticeTarget | None, notice: VoiceNotice) -> bool:
        """Schedule local playback after yielding back to the pipeline."""
        if target is None:
            return False
        self.hass.async_create_task(
            self._async_play(target, notice),
            "Play Hombee Voice notice",
            eager_start=False,
        )
        return True

    async def _async_play(self, target: NoticeTarget, notice: VoiceNotice) -> None:
        try:
            await self.hass.services.async_call(
                "esphome",
                target.service,
                {"notice": f"{notice}_{target.language}"},
                blocking=True,
            )
        except HomeAssistantError:
            _LOGGER.debug(
                "Hombee Voice satellite could not play its notice", exc_info=True
            )
