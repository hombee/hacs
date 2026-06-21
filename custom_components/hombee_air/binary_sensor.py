"""Binary sensors for every read-only boolean Hombee Air register."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
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
    """Sets up binary sensors for read-only boolean registers."""
    runtime = entry.runtime_data
    async_add_entities(
        HombeeAirBinarySensor(runtime, register, entry.title)
        for register in REGISTERS
        if not is_writable(register) and register.is_binary
    )


class HombeeAirBinarySensor(HombeeAirRegisterEntity, BinarySensorEntity):
    """Boolean readout of one register; alarms surface as problems."""

    def __init__(
        self,
        runtime: HombeeAirRuntime,
        register: HombeeAirRegister,
        title: str,
    ) -> None:
        super().__init__(runtime, register, title)
        if register.section == "alarms":
            self._attr_device_class = BinarySensorDeviceClass.PROBLEM

    @property
    def is_on(self) -> bool | None:
        raw = self.raw_value
        return None if raw is None else bool(raw)
