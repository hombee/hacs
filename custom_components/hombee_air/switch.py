"""Switch entities for writable boolean Hombee Air registers."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import HombeeAirConfigEntry
from .entity import HombeeAirRegisterEntity, is_writable
from .registers import REGISTERS


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HombeeAirConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Sets up switches for writable boolean registers."""
    runtime = entry.runtime_data
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
