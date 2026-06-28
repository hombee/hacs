"""Tests for the Hombee Air alarm catalog."""

from __future__ import annotations

from custom_components.hombee_air.registers import (
    KIND_DISCRETE_INPUT,
    REGISTERS,
)

_FORBIDDEN_DESCRIPTION_ABBREVIATIONS = (
    "dod.",
    "nagrz.",
    "pomieszcz.",
    "temp.",
    "termost.",
    "wej./wyj.",
    "went.",
)


def _alarm_code(alarm_code: str | None) -> int | None:
    if alarm_code is None:
        return None
    return int(alarm_code.removeprefix("A"))


def _alarm_description_segments(description: str) -> list[str]:
    return description.split(" - ")


def test_alarm_catalog_covers_plc_alarm_codes() -> None:
    alarms = [
        register
        for register in REGISTERS
        if register.section == "alarms" and register.kind == KIND_DISCRETE_INPUT
    ]
    codes = [_alarm_code(register.alarm_code) for register in alarms]

    assert len(alarms) == 284
    assert sorted(codes) == list(range(1, 285))
    assert len({register.key for register in alarms}) == len(alarms)
    assert len({register.address for register in alarms}) == len(alarms)
    assert {register.address for register in alarms} == set(range(100, 384))
    assert all(
        register.name.endswith(f" ({register.alarm_code})") for register in alarms
    )
    assert all(not register.description.startswith("A") for register in alarms)
    assert all(
        segment == "ESTOP" or not segment[:1].isupper()
        for register in alarms
        for segment in _alarm_description_segments(register.description)
    )
    assert all(
        abbreviation not in register.description
        for register in alarms
        for abbreviation in _FORBIDDEN_DESCRIPTION_ABBREVIATIONS
    )


def test_new_plc_alarm_names_are_mapped() -> None:
    alarms_by_code = {
        _alarm_code(register.alarm_code): register
        for register in REGISTERS
        if register.section == "alarms" and register.kind == KIND_DISCRETE_INPUT
    }

    assert alarms_by_code[151].address == 250
    assert alarms_by_code[151].name == "additional sensor 3 alarm (A151)"
    assert alarms_by_code[152].address == 251
    assert alarms_by_code[152].name == "heat pump 1 alarm (A152)"
    assert alarms_by_code[284].address == 383
    assert alarms_by_code[284].name == "UV lamp fault (A284)"
