"""Select entities for writable enumerated Hombee Air registers."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .brightness import ACTIVITIES, DEFAULT_PROFILE
from .catalog_translations import OPTION_STATE_KEYS
from .const import DOMAIN, LIGHTING_UPDATED
from .coordinator import HombeeAirConfigEntry, HombeeAirRuntime
from .entity import HombeeAirRegisterEntity, is_writable
from .managed_lighting import ManagedLightingManager
from .registers import REGISTERS, HombeeAirRegister


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HombeeAirConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Sets up selects for writable registers with enumerations."""
    runtime = entry.runtime_data
    if isinstance(runtime, ManagedLightingManager):
        known: set[str] = set()

        @callback
        def add_rooms() -> None:
            rooms = (
                {DEFAULT_PROFILE}
                | {
                    runtime.room_key(mapping)
                    for mapping in runtime.mappings.values()
                    if not runtime.room_key(mapping).startswith("light:")
                }
                | set(runtime.profiles)
            )
            for key in rooms - known:
                known.add(key)
                async_add_entities([HombeeLightingActivity(runtime, key)])

        entry.async_on_unload(
            async_dispatcher_connect(hass, LIGHTING_UPDATED, add_rooms)
        )
        add_rooms()
        return
    async_add_entities(
        HombeeAirSelect(runtime, register, entry.title)
        for register in REGISTERS
        if is_writable(register) and not register.is_binary and register.options
    )


class HombeeAirSelect(HombeeAirRegisterEntity, SelectEntity):
    """Writable enumerated value of one register."""

    def __init__(
        self,
        runtime: HombeeAirRuntime,
        register: HombeeAirRegister,
        title: str,
    ) -> None:
        super().__init__(runtime, register, title)
        self._attr_options = [OPTION_STATE_KEYS[label] for _, label in register.options]

    @property
    def current_option(self) -> str | None:
        raw = self.raw_value
        if not isinstance(raw, int):
            return None
        return next(
            (
                OPTION_STATE_KEYS[label]
                for value, label in self._register.options
                if value == raw
            ),
            None,
        )

    async def async_select_option(self, option: str) -> None:
        raw = _raw_value_for_option(self._register, option)
        if raw is None:
            raise HomeAssistantError(
                f"Unknown option for {self._register.key}: {option}"
            )
        await self.async_write_raw(raw)


class HombeeLightingActivity(SelectEntity):
    """Choose room activities from the dashboard or ordinary select actions."""

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_icon = "mdi:lightbulb-auto"
    _attr_device_info = DeviceInfo(identifiers={(DOMAIN, "circadian_lighting")})
    _attr_translation_key = "lighting_activity"

    def __init__(self, manager: ManagedLightingManager, key: str) -> None:
        self.manager = manager
        self._attr_options = list(ACTIVITIES)
        if key != DEFAULT_PROFILE:
            self._attr_options.insert(0, "inherit")
        self.key = key
        self._attr_unique_id = f"hombee_lighting_activity_{key}"
        area = ar.async_get(manager.hass).async_get_area(key)
        self._attr_translation_placeholders = {"room": area.name if area else "Hombee"}

    @property
    def current_option(self) -> str:
        return self.manager.activities.get(
            self.key, "auto" if self.key == DEFAULT_PROFILE else "inherit"
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, LIGHTING_UPDATED, self.async_write_ha_state
            )
        )

    async def async_select_option(self, option: str) -> None:
        await self.manager.async_set_activity(self.key, option)


def _raw_value_for_option(register: HombeeAirRegister, option: str) -> int | None:
    return next(
        (
            value
            for value, label in register.options
            if OPTION_STATE_KEYS[label] == option
        ),
        None,
    )
