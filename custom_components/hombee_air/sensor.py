"""Sensor entities for every read-only numeric Hombee Air register."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
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
    """Sets up sensors for read-only numeric registers."""
    runtime = entry.runtime_data
    async_add_entities(
        HombeeAirSensor(runtime, register, entry.title)
        for register in REGISTERS
        if not is_writable(register) and not register.is_binary
    )


class HombeeAirSensor(HombeeAirRegisterEntity, SensorEntity):
    """Scaled or enumerated readout of one register."""

    def __init__(
        self,
        runtime: HombeeAirRuntime,
        register: HombeeAirRegister,
        title: str,
    ) -> None:
        super().__init__(runtime, register, title)
        if register.options:
            self._attr_device_class = SensorDeviceClass.ENUM
            self._attr_options = [label for _, label in register.options]
        else:
            self._attr_native_unit_of_measurement = register.unit
            self._attr_device_class = _device_class(register)
            self._attr_state_class = _state_class(register)
            self._attr_suggested_display_precision = register.precision

    @property
    def native_value(self) -> float | str | None:
        raw = self.raw_value
        if not isinstance(raw, int):
            return None
        if self._register.options:
            return next(
                (label for value, label in self._register.options if value == raw),
                None,
            )
        return self._register.decode_numeric(raw)


def _device_class(register: HombeeAirRegister) -> SensorDeviceClass | None:
    if register.device_class is None:
        return None
    try:
        return SensorDeviceClass(register.device_class)
    except ValueError:
        return None


def _state_class(register: HombeeAirRegister) -> SensorStateClass | None:
    if register.state_class is None:
        return None
    try:
        return SensorStateClass(register.state_class)
    except ValueError:
        return None
