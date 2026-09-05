"""Number entities for writable numeric Hombee Air registers."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import HombeeAirConfigEntry, HombeeAirRuntime
from .entity import HombeeAirRegisterEntity, is_writable
from .registers import REGISTERS, HombeeAirRegister


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HombeeAirConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Sets up numbers for writable registers without enumerations."""
    runtime = entry.runtime_data
    async_add_entities(
        HombeeAirNumber(runtime, register, entry.title)
        for register in REGISTERS
        if is_writable(register) and not register.is_binary and not register.options
    )


class HombeeAirNumber(HombeeAirRegisterEntity, NumberEntity):
    """Writable scaled value of one register."""

    def __init__(
        self,
        runtime: HombeeAirRuntime,
        register: HombeeAirRegister,
        title: str,
    ) -> None:
        super().__init__(runtime, register, title)
        self._attr_native_unit_of_measurement = register.unit
        self._attr_native_step = register.scale if register.scale != 1 else 1
        if register.min_value is not None:
            self._attr_native_min_value = register.min_value
        if register.max_value is not None:
            self._attr_native_max_value = register.max_value

    @property
    def native_value(self) -> float | None:
        raw = self.raw_value
        if not isinstance(raw, int):
            return None
        return self._register.decode_numeric(raw)

    async def async_set_native_value(self, value: float) -> None:
        await self.async_write_raw(self._register.encode_numeric(value))
