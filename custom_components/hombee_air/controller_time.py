"""Controller clock synchronization for the Hombee Air unit."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import datetime
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import CONTROLLER_TIME_SYNC_THRESHOLD
from .modbus_client import HombeeAirModbusError
from .registers import REGISTERS_BY_KEY

if TYPE_CHECKING:
    from .coordinator import HombeeAirRuntime

_LOGGER = logging.getLogger(__name__)

_KEY_MINUTE = "controller_minute"
_KEY_HOUR = "controller_hour"
_KEY_DAY = "controller_day"
_KEY_MONTH = "controller_month"
_KEY_YEAR = "controller_year"
_KEY_SAVE = "controller_time_save"

_CLOCK_KEYS = (
    _KEY_MINUTE,
    _KEY_HOUR,
    _KEY_DAY,
    _KEY_MONTH,
    _KEY_YEAR,
)


def async_start_controller_time_sync(
    hass: HomeAssistant, runtime: HombeeAirRuntime
) -> None:
    """Start syncing the unit clock after slow coordinator updates."""
    if runtime.controller_time_remove_listener is not None:
        return

    def _schedule_sync() -> None:
        _async_schedule_controller_time_sync(hass, runtime)

    runtime.controller_time_remove_listener = runtime.slow.async_add_listener(
        _schedule_sync
    )
    _schedule_sync()


async def async_stop_controller_time_sync(runtime: HombeeAirRuntime) -> None:
    """Stop controller clock syncing for an unloaded config entry."""
    if runtime.controller_time_remove_listener is not None:
        runtime.controller_time_remove_listener()
        runtime.controller_time_remove_listener = None

    task = runtime.controller_time_sync_task
    if task is not None and not task.done():
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    runtime.controller_time_sync_task = None


def _async_schedule_controller_time_sync(
    hass: HomeAssistant, runtime: HombeeAirRuntime
) -> None:
    task = runtime.controller_time_sync_task
    if task is not None and not task.done():
        return

    now = dt_util.now()
    if not _needs_sync(runtime.slow.data, now):
        return

    runtime.controller_time_sync_task = hass.async_create_task(
        _async_sync_controller_time(runtime)
    )


async def _async_sync_controller_time(runtime: HombeeAirRuntime) -> None:
    try:
        now = dt_util.now()
        for key, raw_value in _clock_payload(now):
            await runtime.async_write(REGISTERS_BY_KEY[key], raw_value)
    except HombeeAirModbusError as error:
        _LOGGER.warning("Failed to synchronize Hombee Air controller time: %s", error)
    finally:
        runtime.controller_time_sync_task = None


def _needs_sync(
    raw: dict[str, int | bool] | None,
    now: datetime,
) -> bool:
    if raw is None or any(key not in raw for key in _CLOCK_KEYS):
        return False
    controller_time = _controller_datetime(raw, now)
    if controller_time is None:
        return True
    return abs(now - controller_time) > CONTROLLER_TIME_SYNC_THRESHOLD


def _controller_datetime(
    raw: dict[str, int | bool] | None,
    now: datetime,
) -> datetime | None:
    if raw is None:
        return None

    values: dict[str, int] = {}
    for key in _CLOCK_KEYS:
        value = raw.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        values[key] = value

    try:
        return datetime(
            values[_KEY_YEAR] + 2000,
            values[_KEY_MONTH],
            values[_KEY_DAY],
            values[_KEY_HOUR],
            values[_KEY_MINUTE],
            tzinfo=now.tzinfo,
        )
    except ValueError:
        return None


def _clock_payload(now: datetime) -> tuple[tuple[str, int], ...]:
    return (
        (_KEY_YEAR, now.year - 2000),
        (_KEY_MONTH, now.month),
        (_KEY_DAY, now.day),
        (_KEY_HOUR, now.hour),
        (_KEY_MINUTE, now.minute),
        (_KEY_SAVE, 1),
    )
