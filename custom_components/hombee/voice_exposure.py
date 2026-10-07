"""Keep Hombee implementation entities outside voice assistant exposure."""

from __future__ import annotations

from typing import Any

from homeassistant.components.homeassistant.exposed_entities import (
    KNOWN_ASSISTANTS,
    async_expose_entity,
)
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

_DATA_SETUP = "hombee_voice_exposure_setup"
_RESTORE_SCENE_PREFIX = "scene.hombee_restore_"


def voice_options(entry: er.RegistryEntry) -> dict[str, dict[str, Any]]:
    """Snapshot assistant namespaces without unrelated integration options."""
    return {
        assistant: dict(entry.options[assistant])
        for assistant in KNOWN_ASSISTANTS
        if assistant in entry.options
    }


def voice_aliases(entry: er.RegistryEntry) -> tuple[str | None, ...]:
    """Preserve alias order and the computed entity-name placeholder in storage."""
    return tuple(
        None if alias is er.COMPUTED_NAME else alias for alias in entry.aliases
    )


@callback
def async_apply_voice_settings(
    hass: HomeAssistant,
    entity_id: str,
    options: dict[str, dict[str, Any]],
    aliases: tuple[str | None, ...],
    *,
    overwrite: bool,
) -> None:
    """Transfer settings while retaining existing public choices on migration."""
    registry = er.async_get(hass)
    entry = registry.async_get(entity_id)
    for assistant in KNOWN_ASSISTANTS:
        settings = options.get(assistant)
        if not overwrite:
            if settings is None:
                continue
            settings = settings | dict(entry.options.get(assistant, {}))
        registry.async_update_entity_options(entity_id, assistant, settings)
    if overwrite or entry.aliases == [er.COMPUTED_NAME]:
        registry.async_update_entity(
            entity_id,
            aliases=[er.COMPUTED_NAME if alias is None else alias for alias in aliases],
        )


@callback
def async_hide_from_voice(hass: HomeAssistant, entity_id: str) -> None:
    """Hidden implementation entities must not retain explicit exposure."""
    for assistant in KNOWN_ASSISTANTS:
        async_expose_entity(hass, assistant, entity_id, False)


@callback
def async_setup_voice_exposure(hass: HomeAssistant) -> None:
    """Protect existing and newly created transient automation restore scenes."""
    if hass.data.get(_DATA_SETUP):
        return
    hass.data[_DATA_SETUP] = True

    @callback
    def scene_changed(event) -> None:
        entity_id = event.data["entity_id"]
        if entity_id.startswith(_RESTORE_SCENE_PREFIX) and event.data["new_state"]:
            async_hide_from_voice(hass, entity_id)

    hass.bus.async_listen(EVENT_STATE_CHANGED, scene_changed)
    for entity_id in hass.states.async_entity_ids("scene"):
        if entity_id.startswith(_RESTORE_SCENE_PREFIX):
            async_hide_from_voice(hass, entity_id)
