"""Paced Modbus TCP client for the Hombee Air unit.

Reads are batched into contiguous chunks per register kind. Writes go
through a coalescing queue: at most one Modbus write per second leaves the
queue, and rapid successive writes to the same point collapse into a single
write of the latest value. Entities provide instant feedback through
optimistic state while the queue drains.
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass, field
from time import monotonic

from pymodbus.client import AsyncModbusTcpClient

from .const import IO_PACING_SECONDS, MODBUS_UNIT_ID, WRITE_PACING_SECONDS
from .registers import (
    KIND_COIL,
    KIND_DISCRETE_INPUT,
    KIND_HOLDING_REGISTER,
    KIND_INPUT_REGISTER,
    HombeeAirRegister,
)

_LOGGER = logging.getLogger(__name__)

_BIT_KINDS = (KIND_COIL, KIND_DISCRETE_INPUT)
_SIGNED_VALUE_TYPES = ("int", "real")

_MAX_READ_SPAN = {
    KIND_COIL: 120,
    KIND_DISCRETE_INPUT: 120,
    KIND_HOLDING_REGISTER: 80,
    KIND_INPUT_REGISTER: 80,
}
_MAX_READ_GAP = 4


class HombeeAirModbusError(Exception):
    """Raised when a Modbus operation fails."""


@dataclass(frozen=True, slots=True)
class ReadChunk:
    """One contiguous Modbus read covering several catalog points."""

    kind: str
    start: int
    count: int
    points: tuple[HombeeAirRegister, ...]


def build_read_plan(
    registers: Iterable[HombeeAirRegister],
) -> tuple[ReadChunk, ...]:
    """Groups registers into contiguous reads per kind.

    Mirrors the chunking used by the Hombee app's direct Modbus path:
    points of the same kind merge while the address gap stays within 4 and
    the chunk span stays below the per-kind read limit.
    """
    chunks: list[ReadChunk] = []
    for kind in (
        KIND_COIL,
        KIND_DISCRETE_INPUT,
        KIND_HOLDING_REGISTER,
        KIND_INPUT_REGISTER,
    ):
        sorted_points = sorted(
            (point for point in registers if point.kind == kind),
            key=lambda point: point.address,
        )
        if not sorted_points:
            continue
        current = [sorted_points[0]]
        for point in sorted_points[1:]:
            span = point.end_address - current[0].address
            gap = point.address - current[-1].end_address
            if gap <= _MAX_READ_GAP and span < _MAX_READ_SPAN[kind]:
                current.append(point)
            else:
                chunks.append(_chunk(kind, current))
                current = [point]
        chunks.append(_chunk(kind, current))
    return tuple(chunks)


def _chunk(kind: str, points: list[HombeeAirRegister]) -> ReadChunk:
    start = points[0].address
    count = points[-1].end_address - start + 1
    return ReadChunk(kind=kind, start=start, count=count, points=tuple(points))


@dataclass(slots=True)
class _PendingWrite:
    """Latest queued value for one Modbus point plus its waiters."""

    register: HombeeAirRegister
    raw_value: int
    futures: list[asyncio.Future[None]] = field(default_factory=list)


class HombeeAirModbusClient:
    """Serialized, paced Modbus TCP access to one Hombee Air unit."""

    def __init__(self, host: str, port: int) -> None:
        self._client = AsyncModbusTcpClient(host, port=port)
        self._io_lock = asyncio.Lock()
        self._last_io = monotonic() - IO_PACING_SECONDS
        self._last_write = monotonic() - WRITE_PACING_SECONDS
        self._write_queue: OrderedDict[tuple[str, int], _PendingWrite] = OrderedDict()
        self._active_write: _PendingWrite | None = None
        self._write_worker: asyncio.Task[None] | None = None
        self._closed = False

    async def async_connect(self) -> None:
        """Opens the TCP connection, raising on failure."""
        if not await self._client.connect():
            raise HombeeAirModbusError(
                f"Cannot connect to {self._client.comm_params.host}"
            )

    async def async_close(self) -> None:
        """Closes the connection and fails all queued writes."""
        self._closed = True
        worker = self._write_worker
        if self._write_worker is not None:
            self._write_worker.cancel()
        if self._active_write is not None:
            _reject_waiters(
                self._active_write, HombeeAirModbusError("Connection closed")
            )
            self._active_write = None
        while self._write_queue:
            _, pending = self._write_queue.popitem(last=False)
            _reject_waiters(pending, HombeeAirModbusError("Connection closed"))
        self._client.close()
        if worker is not None and worker is not asyncio.current_task():
            with suppress(asyncio.CancelledError):
                await worker
        self._write_worker = None

    async def async_read(self, plan: Iterable[ReadChunk]) -> dict[str, int | bool]:
        """Executes a read plan and returns raw values keyed by point key."""
        result: dict[str, int | bool] = {}
        for chunk in plan:
            if chunk.kind in _BIT_KINDS:
                bits = await self._read_bits(chunk)
                for point in chunk.points:
                    result[point.key] = bits[point.address - chunk.start]
            else:
                words = await self._read_words(chunk)
                for point in chunk.points:
                    offset = point.address - chunk.start
                    raw = point.decode_raw_words(
                        words[offset : offset + point.word_count]
                    )
                    result[point.key] = _signed(point, raw)
        return result

    def async_write(
        self, register: HombeeAirRegister, raw_value: int
    ) -> asyncio.Future[None]:
        """Queues a write; resolves once the value reaches the unit.

        Writes to the same point coalesce: only the latest queued value is
        written, and every queued waiter resolves with that flush.
        """
        if self._closed:
            raise HombeeAirModbusError("Connection closed")
        loop = asyncio.get_running_loop()
        future: asyncio.Future[None] = loop.create_future()
        queue_key = (register.kind, register.address)
        pending = self._write_queue.get(queue_key)
        if pending is None:
            self._write_queue[queue_key] = _PendingWrite(
                register=register, raw_value=raw_value, futures=[future]
            )
        else:
            pending.raw_value = raw_value
            pending.futures.append(future)
        if self._write_worker is None or self._write_worker.done():
            self._write_worker = loop.create_task(self._drain_writes())
        return future

    async def _drain_writes(self) -> None:
        while self._write_queue:
            wait = self._last_write + WRITE_PACING_SECONDS - monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            if not self._write_queue:
                return
            _, pending = self._write_queue.popitem(last=False)
            self._active_write = pending
            self._last_write = monotonic()
            try:
                await self._write_point(pending.register, pending.raw_value)
            except asyncio.CancelledError:
                _reject_waiters(pending, HombeeAirModbusError("Connection closed"))
                raise
            except Exception as error:  # noqa: BLE001 - propagated to waiters
                _reject_waiters(pending, HombeeAirModbusError(str(error)))
            else:
                for future in pending.futures:
                    if not future.done():
                        future.set_result(None)
            finally:
                if self._active_write is pending:
                    self._active_write = None

    async def _write_point(self, register: HombeeAirRegister, raw_value: int) -> None:
        async with self._paced_io():
            if register.kind == KIND_COIL:
                response = await self._client.write_coil(
                    register.address,
                    bool(raw_value),
                    device_id=MODBUS_UNIT_ID,
                )
            elif register.kind == KIND_HOLDING_REGISTER:
                response = await self._client.write_register(
                    register.address,
                    raw_value & 0xFFFF,
                    device_id=MODBUS_UNIT_ID,
                )
            else:
                raise HombeeAirModbusError(
                    f"Point {register.key} is not writable over Modbus"
                )
        if response.isError():
            raise HombeeAirModbusError(f"Write to {register.key} failed: {response}")

    async def _read_bits(self, chunk: ReadChunk) -> list[bool]:
        async with self._paced_io():
            if chunk.kind == KIND_COIL:
                response = await self._client.read_coils(
                    chunk.start, count=chunk.count, device_id=MODBUS_UNIT_ID
                )
            else:
                response = await self._client.read_discrete_inputs(
                    chunk.start, count=chunk.count, device_id=MODBUS_UNIT_ID
                )
        if response.isError():
            raise HombeeAirModbusError(
                f"Read of {chunk.kind} {chunk.start} failed: {response}"
            )
        return list(response.bits)

    async def _read_words(self, chunk: ReadChunk) -> list[int]:
        async with self._paced_io():
            if chunk.kind == KIND_HOLDING_REGISTER:
                response = await self._client.read_holding_registers(
                    chunk.start, count=chunk.count, device_id=MODBUS_UNIT_ID
                )
            else:
                response = await self._client.read_input_registers(
                    chunk.start, count=chunk.count, device_id=MODBUS_UNIT_ID
                )
        if response.isError():
            raise HombeeAirModbusError(
                f"Read of {chunk.kind} {chunk.start} failed: {response}"
            )
        return list(response.registers)

    def _paced_io(self) -> _PacedIo:
        return _PacedIo(self)


class _PacedIo:
    """Async context manager enforcing connection and pacing."""

    def __init__(self, client: HombeeAirModbusClient) -> None:
        self._owner = client

    async def __aenter__(self) -> None:
        owner = self._owner
        await owner._io_lock.acquire()
        try:
            wait = owner._last_io + IO_PACING_SECONDS - monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            if not owner._client.connected:
                await owner.async_connect()
        except BaseException:
            owner._io_lock.release()
            raise

    async def __aexit__(self, *exc_info: object) -> None:
        self._owner._last_io = monotonic()
        self._owner._io_lock.release()


def _signed(point: HombeeAirRegister, raw: int) -> int:
    """Interprets single-word values as signed when the catalog says so."""
    if (
        point.word_count == 1
        and point.value_type in _SIGNED_VALUE_TYPES
        and raw >= 0x8000
    ):
        return raw - 0x10000
    return raw


def _reject_waiters(pending: _PendingWrite, error: HombeeAirModbusError) -> None:
    """Fails every waiter for a queued or active write."""
    for future in pending.futures:
        if not future.done():
            future.set_exception(error)
