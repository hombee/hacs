"""Update coordinators for the Hombee Air integration.

Two cadences: live readings (input registers, coils, discrete inputs)
refresh every 10 seconds; setpoints and configuration (holding registers)
refresh every minute and immediately after writes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .const import DOMAIN, FAST_SCAN_INTERVAL, SLOW_SCAN_INTERVAL
from .modbus_client import (
    HombeeAirModbusClient,
    HombeeAirModbusError,
    ReadChunk,
    build_read_plan,
)
from .registers import KIND_HOLDING_REGISTER, REGISTERS, HombeeAirRegister

_LOGGER = logging.getLogger(__name__)

_REFRESH_AFTER_WRITE_COOLDOWN = 1.0

type HombeeAirConfigEntry = ConfigEntry[HombeeAirRuntime]


class HombeeAirCoordinator(DataUpdateCoordinator[dict[str, int | bool]]):
    """Polls one cadence bucket of the register catalog."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: HombeeAirConfigEntry,
        client: HombeeAirModbusClient,
        name: str,
        plan: tuple[ReadChunk, ...],
        interval: timedelta,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{name}",
            update_interval=interval,
            request_refresh_debouncer=Debouncer(
                hass,
                _LOGGER,
                cooldown=_REFRESH_AFTER_WRITE_COOLDOWN,
                immediate=False,
            ),
        )
        self._client = client
        self._plan = plan

    async def _async_update_data(self) -> dict[str, int | bool]:
        try:
            return await self._client.async_read(self._plan)
        except HombeeAirModbusError as error:
            raise UpdateFailed(str(error)) from error


@dataclass(slots=True)
class HombeeAirRuntime:
    """Runtime objects shared by all platforms of one config entry."""

    slug: str
    client: HombeeAirModbusClient
    fast: HombeeAirCoordinator
    slow: HombeeAirCoordinator

    def coordinator_for(self, register: HombeeAirRegister) -> HombeeAirCoordinator:
        """Returns the coordinator polling the given register."""
        if register.kind == KIND_HOLDING_REGISTER:
            return self.slow
        return self.fast

    def raw_value(self, key: str) -> int | bool | None:
        """Latest raw value of a register from either coordinator."""
        for coordinator in (self.fast, self.slow):
            data = coordinator.data
            if data is not None and key in data:
                return data[key]
        return None

    async def async_write(self, register: HombeeAirRegister, raw_value: int) -> None:
        """Writes a point and refreshes the owning coordinator."""
        await self.client.async_write(register, raw_value)
        await self.coordinator_for(register).async_request_refresh()


def create_runtime(
    hass: HomeAssistant,
    entry: HombeeAirConfigEntry,
    slug: str,
    client: HombeeAirModbusClient,
) -> HombeeAirRuntime:
    """Builds the two coordinators covering the whole register catalog."""
    holding = tuple(
        register for register in REGISTERS if register.kind == KIND_HOLDING_REGISTER
    )
    live = tuple(
        register for register in REGISTERS if register.kind != KIND_HOLDING_REGISTER
    )
    return HombeeAirRuntime(
        slug=slug,
        client=client,
        fast=HombeeAirCoordinator(
            hass,
            entry,
            client,
            name="live",
            plan=build_read_plan(live),
            interval=FAST_SCAN_INTERVAL,
        ),
        slow=HombeeAirCoordinator(
            hass,
            entry,
            client,
            name="setpoints",
            plan=build_read_plan(holding),
            interval=SLOW_SCAN_INTERVAL,
        ),
    )
