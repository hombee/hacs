"""Tests for the write coalescing queue.

The unit tolerates at most one write per second; rapid writes to the same
point must collapse into a single write of the latest value.
"""

from __future__ import annotations

import asyncio
from time import monotonic

import pytest

from custom_components.hombee_air import modbus_client
from custom_components.hombee_air.modbus_client import (
    HombeeAirModbusClient,
    HombeeAirModbusError,
)
from custom_components.hombee_air.registers import REGISTERS_BY_KEY

_PACING = 0.05

_MANUAL_TEMPERATURE = REGISTERS_BY_KEY["manual_temperature_setpoint"]
_MANUAL_HUMIDITY = REGISTERS_BY_KEY["manual_humidity_setpoint"]


@pytest.fixture(autouse=True)
def _fast_pacing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(modbus_client, "WRITE_PACING_SECONDS", _PACING)


class _RecordingClient(HombeeAirModbusClient):
    """Client whose physical writes are recorded instead of sent."""

    def __init__(self) -> None:
        super().__init__("127.0.0.1", 502)
        self.writes: list[tuple[str, int, float]] = []
        self.fail = False

    async def _write_point(self, register, raw_value) -> None:  # type: ignore[override]
        if self.fail:
            raise HombeeAirModbusError("boom")
        self.writes.append((register.key, raw_value, monotonic()))


class _BlockingWriteClient(HombeeAirModbusClient):
    """Client that pauses after a write has left the queue."""

    def __init__(self) -> None:
        super().__init__("127.0.0.1", 502)
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def _write_point(self, register, raw_value) -> None:  # type: ignore[override]
        self.started.set()
        await self.release.wait()


async def test_burst_to_one_point_collapses_to_latest_value() -> None:
    client = _RecordingClient()
    futures = [
        client.async_write(_MANUAL_TEMPERATURE, raw) for raw in (200, 210, 220, 235)
    ]
    await asyncio.gather(*futures)
    assert [(key, raw) for key, raw, _ in client.writes] == [
        ("manual_temperature_setpoint", 235)
    ]


async def test_writes_to_distinct_points_are_paced() -> None:
    client = _RecordingClient()
    await asyncio.gather(
        client.async_write(_MANUAL_TEMPERATURE, 200),
        client.async_write(_MANUAL_HUMIDITY, 450),
    )
    assert len(client.writes) == 2
    spacing = client.writes[1][2] - client.writes[0][2]
    assert spacing >= _PACING * 0.9


async def test_failed_flush_rejects_every_waiter() -> None:
    client = _RecordingClient()
    client.fail = True
    futures = [
        client.async_write(_MANUAL_TEMPERATURE, 200),
        client.async_write(_MANUAL_TEMPERATURE, 210),
    ]
    results = await asyncio.gather(*futures, return_exceptions=True)
    assert all(isinstance(result, HombeeAirModbusError) for result in results)


async def test_close_rejects_pending_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(modbus_client, "WRITE_PACING_SECONDS", 60.0)
    client = _RecordingClient()
    first = client.async_write(_MANUAL_TEMPERATURE, 200)
    second = client.async_write(_MANUAL_HUMIDITY, 450)
    await first
    await client.async_close()
    with pytest.raises(HombeeAirModbusError):
        await second


async def test_close_rejects_active_write() -> None:
    client = _BlockingWriteClient()
    write = client.async_write(_MANUAL_TEMPERATURE, 200)
    await client.started.wait()

    await client.async_close()

    with pytest.raises(HombeeAirModbusError):
        await write
