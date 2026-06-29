"""Shared entity base for the Hombee Air integration."""

from __future__ import annotations

from time import monotonic

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MODEL, OPTIMISTIC_HOLD_SECONDS
from .coordinator import HombeeAirCoordinator, HombeeAirRuntime
from .modbus_client import HombeeAirModbusError
from .registers import (
    TIER_ADVANCED_WRITABLE,
    TIER_READ_ONLY,
    TIER_SERVICE_WRITABLE,
    HombeeAirRegister,
)

_DIAGNOSTIC_SECTIONS = ("alarms", "advanced_diagnostics")


def device_info(slug: str, title: str) -> DeviceInfo:
    """Device registry payload for one Hombee Air unit."""
    return DeviceInfo(
        identifiers={(DOMAIN, slug)},
        name=title,
        manufacturer=MANUFACTURER,
        model=MODEL,
    )


def is_writable(register: HombeeAirRegister) -> bool:
    """Whether a point accepts writes on any access tier."""
    return register.writable or register.access_tier != TIER_READ_ONLY


class HombeeAirRegisterEntity(CoordinatorEntity[HombeeAirCoordinator]):
    """One entity backed by one catalog register.

    Writes apply optimistic state immediately (the write queue flushes at
    most one Modbus write per second) and roll back if the flush fails.
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        runtime: HombeeAirRuntime,
        register: HombeeAirRegister,
        title: str,
    ) -> None:
        super().__init__(runtime.coordinator_for(register))
        self._runtime = runtime
        self._register = register
        self._optimistic_raw: int | bool | None = None
        self._optimistic_deadline: float | None = None
        self._attr_unique_id = f"{DOMAIN}_{runtime.slug}_{register.key}"
        self._attr_translation_key = register.key
        self._attr_device_info = device_info(runtime.slug, title)
        self._attr_entity_category = _entity_category(register)
        if register.access_tier == TIER_ADVANCED_WRITABLE:
            self._attr_entity_registry_enabled_default = False

    @property
    def suggested_object_id(self) -> str:
        """Keep entity ids stable while display names are translated."""
        return self._register.name

    @property
    def raw_value(self) -> int | bool | None:
        """Optimistic raw value when a write is pending, else polled."""
        self._prune_optimistic_raw()
        if self._optimistic_raw is not None:
            return self._optimistic_raw
        raw = self.coordinator.data.get(self._register.key)
        if raw == self._register.unavailable_raw_value:
            return None
        return raw

    async def async_write_raw(self, raw_value: int | bool) -> None:
        """Optimistically applies and queues a write for this point."""
        self._optimistic_raw = raw_value
        self._optimistic_deadline = monotonic() + OPTIMISTIC_HOLD_SECONDS
        self.async_write_ha_state()
        try:
            await self._runtime.async_write(self._register, int(raw_value))
        except HombeeAirModbusError as error:
            self._clear_optimistic_raw()
            self.async_write_ha_state()
            raise HomeAssistantError(
                f"Writing {self._register.key} failed: {error}"
            ) from error

    def _handle_coordinator_update(self) -> None:
        self._prune_optimistic_raw()
        super()._handle_coordinator_update()

    def _prune_optimistic_raw(self) -> None:
        if self._optimistic_raw is None:
            self._optimistic_deadline = None
            return
        if self.coordinator.data.get(self._register.key) == self._optimistic_raw:
            self._clear_optimistic_raw()
            return
        if (
            self._optimistic_deadline is not None
            and monotonic() >= self._optimistic_deadline
        ):
            self._clear_optimistic_raw()

    def _clear_optimistic_raw(self) -> None:
        self._optimistic_raw = None
        self._optimistic_deadline = None


def _entity_category(register: HombeeAirRegister) -> EntityCategory | None:
    if register.access_tier in (
        TIER_SERVICE_WRITABLE,
        TIER_ADVANCED_WRITABLE,
    ):
        return EntityCategory.CONFIG
    if (
        register.access_tier == TIER_READ_ONLY
        and register.section in _DIAGNOSTIC_SECTIONS
    ):
        return EntityCategory.DIAGNOSTIC
    return None
