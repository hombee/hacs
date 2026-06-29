"""Tests for native Home Assistant translation coverage."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from custom_components.hombee_air.catalog_translations import (
    OPTION_STATE_KEYS,
    POLISH_ENTITY_NAMES,
    POLISH_OPTION_STATES,
)
from custom_components.hombee_air.entity import is_writable
from custom_components.hombee_air.registers import REGISTERS, HombeeAirRegister

COMPONENT_PATH = Path("custom_components/hombee_air")


def _load_translation(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _platform(register: HombeeAirRegister) -> str:
    writable = is_writable(register)
    if not writable and register.is_binary:
        return "binary_sensor"
    if not writable:
        return "sensor"
    if register.is_binary:
        return "switch"
    if register.options:
        return "select"
    return "number"


def _entity_count(translation: dict[str, Any]) -> int:
    return sum(len(platform) for platform in translation["entity"].values())


def test_native_entity_translation_files_cover_register_catalog() -> None:
    strings = _load_translation(COMPONENT_PATH / "strings.json")
    english = _load_translation(COMPONENT_PATH / "translations/en.json")
    polish = _load_translation(COMPONENT_PATH / "translations/pl.json")

    assert _entity_count(strings) == len(REGISTERS)
    assert _entity_count(english) == len(REGISTERS)
    assert _entity_count(polish) == len(REGISTERS)
    assert set(POLISH_ENTITY_NAMES) == {register.key for register in REGISTERS}

    for register in REGISTERS:
        platform = _platform(register)
        assert strings["entity"][platform][register.key]["name"] == register.name
        assert english["entity"][platform][register.key]["name"] == register.name
        expected_polish_name = POLISH_ENTITY_NAMES[register.key]
        if register.alarm_code is not None:
            expected_polish_name = f"{expected_polish_name} ({register.alarm_code})"
        assert polish["entity"][platform][register.key]["name"] == expected_polish_name


def test_native_entity_state_translations_cover_register_options() -> None:
    strings = _load_translation(COMPONENT_PATH / "strings.json")
    english = _load_translation(COMPONENT_PATH / "translations/en.json")
    polish = _load_translation(COMPONENT_PATH / "translations/pl.json")
    option_labels = {label for register in REGISTERS for _, label in register.options}

    assert set(OPTION_STATE_KEYS) == option_labels
    assert set(POLISH_OPTION_STATES) == option_labels
    for register in REGISTERS:
        platform = _platform(register)
        entry_path = ["entity", platform, register.key]
        strings_entry = strings[entry_path[0]][entry_path[1]][entry_path[2]]
        english_entry = english[entry_path[0]][entry_path[1]][entry_path[2]]
        polish_entry = polish[entry_path[0]][entry_path[1]][entry_path[2]]
        if not register.options:
            assert "state" not in strings_entry
            assert "state" not in english_entry
            assert "state" not in polish_entry
            continue

        expected_english = {
            OPTION_STATE_KEYS[label]: label for _, label in register.options
        }
        expected_polish = {
            OPTION_STATE_KEYS[label]: POLISH_OPTION_STATES[label]
            for _, label in register.options
        }
        assert strings_entry["state"] == expected_english
        assert english_entry["state"] == expected_english
        assert polish_entry["state"] == expected_polish
