"""Switch entities for writable boolean Hombee Air registers."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import HombeeAirConfigEntry
from .entity import HombeeAirRegisterEntity, is_writable
from .managed_lighting import LIGHTING_UPDATED, ManagedLightingManager
from .registers import REGISTERS


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HombeeAirConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Sets up switches for writable boolean registers."""
    runtime = entry.runtime_data
    if isinstance(runtime, ManagedLightingManager):
        async_add_entities([HombeeCircadianSwitch(runtime)])
        return
    async_add_entities(
        HombeeAirSwitch(runtime, register, entry.title)
        for register in REGISTERS
        if is_writable(register) and register.is_binary
    )


class HombeeAirSwitch(HombeeAirRegisterEntity, SwitchEntity):
    """Writable boolean value of one register."""

    @property
    def is_on(self) -> bool | None:
        raw = self.raw_value
        return None if raw is None else bool(raw)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.async_write_raw(1)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.async_write_raw(0)


class HombeeCircadianSwitch(SwitchEntity):
    """Global circadian policy exposed through the standard HA service API."""

    _attr_unique_id = "hombee_circadian_lighting"
    _attr_name = "Circadian lighting"
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_icon = "mdi:theme-light-dark"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_device_info = DeviceInfo(
        identifiers={(DOMAIN, "circadian_lighting")},
        name="Hombee",
        manufacturer="Hombee",
        model="Circadian lighting",
    )

    def __init__(self, manager: ManagedLightingManager) -> None:
        self.manager = manager

    @property
    def is_on(self) -> bool:
        return self.manager.enabled

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, LIGHTING_UPDATED, self.async_write_ha_state
            )
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.manager.async_set_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.manager.async_set_enabled(False)
