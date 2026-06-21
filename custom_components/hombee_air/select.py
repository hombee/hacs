"""Select entities for writable enumerated Hombee Air registers."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import HombeeAirConfigEntry, HombeeAirRuntime
from .entity import HombeeAirRegisterEntity, is_writable
from .registers import REGISTERS, HombeeAirRegister


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HombeeAirConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Sets up selects for writable registers with enumerations."""
    runtime = entry.runtime_data
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
        self._attr_options = [label for _, label in register.options]

    @property
    def current_option(self) -> str | None:
        raw = self.raw_value
        if not isinstance(raw, int):
            return None
        return next(
            (label for value, label in self._register.options if value == raw),
            None,
        )

    async def async_select_option(self, option: str) -> None:
        raw = next(
            (value for value, label in self._register.options if label == option),
            None,
        )
        if raw is None:
            raise HomeAssistantError(
                f"Unknown option for {self._register.key}: {option}"
            )
        await self.async_write_raw(raw)
