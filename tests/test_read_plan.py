"""Tests for the Modbus read chunk planner."""

from __future__ import annotations

from custom_components.hombee_air.modbus_client import build_read_plan
from custom_components.hombee_air.registers import (
    KIND_COIL,
    KIND_HOLDING_REGISTER,
    KIND_INPUT_REGISTER,
    REGISTERS,
    HombeeAirRegister,
)


def _register(
    key: str,
    address: int,
    kind: str = KIND_INPUT_REGISTER,
    value_type: str = "uint",
) -> HombeeAirRegister:
    return HombeeAirRegister(
        key=key,
        name=key,
        address=address,
        kind=kind,
        value_type=value_type,
        access_tier="read_only",
        section="overview",
        writable=False,
        scale=1.0,
        precision=None,
        unit=None,
        device_class=None,
        state_class=None,
        min_value=None,
        max_value=None,
        unavailable_raw_value=None,
        requires_confirmation=False,
        description=key,
    )


def test_adjacent_points_merge_into_one_chunk() -> None:
    plan = build_read_plan([_register("a", 0), _register("b", 2), _register("c", 6)])
    assert len(plan) == 1
    assert plan[0].start == 0
    assert plan[0].count == 7


def test_gap_above_four_splits_chunks() -> None:
    plan = build_read_plan([_register("a", 0), _register("b", 6)])
    assert [chunk.start for chunk in plan] == [0, 6]


def test_register_span_limit_splits_chunks() -> None:
    points = [_register(f"p{i}", i * 4) for i in range(25)]
    plan = build_read_plan(points)
    assert all(chunk.count <= 80 for chunk in plan)
    assert len(plan) > 1


def test_bit_span_limit_is_120() -> None:
    points = [
        _register(f"c{i}", i * 4, kind=KIND_COIL, value_type="bool") for i in range(35)
    ]
    plan = build_read_plan(points)
    assert all(chunk.count <= 120 for chunk in plan)


def test_uint32_point_extends_chunk_count() -> None:
    plan = build_read_plan(
        [
            _register(
                "wide",
                0,
                kind=KIND_HOLDING_REGISTER,
                value_type="uint32",
            )
        ]
    )
    assert plan[0].count == 2


def test_full_catalog_plan_covers_every_register() -> None:
    plan = build_read_plan(REGISTERS)
    covered = {point.key for chunk in plan for point in chunk.points}
    assert covered == {register.key for register in REGISTERS}
    for chunk in plan:
        for point in chunk.points:
            assert point.address >= chunk.start
            assert point.end_address < chunk.start + chunk.count
