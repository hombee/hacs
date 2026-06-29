from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from custom_components.hombee_air.catalog_translations import (  # noqa: E402
    OPTION_STATE_KEYS,
    POLISH_ENTITY_NAMES,
    POLISH_OPTION_STATES,
)
from custom_components.hombee_air.entity import is_writable  # noqa: E402
from custom_components.hombee_air.registers import (  # noqa: E402
    REGISTERS,
    HombeeAirRegister,
)

COMPONENT_PATH = ROOT / "custom_components/hombee_air"
TRANSLATIONS_PATH = COMPONENT_PATH / "translations"

EN_ACTIVE_ALARM_ISSUE = {
    "title": "Active Hombee Air alarm {alarm_code}",
    "description": "{unit} reports active alarm {alarm_code}.",
}

PL_ACTIVE_ALARM_ISSUE = {
    "title": "Aktywny alarm Hombee Air {alarm_code}",
    "description": "{unit} zgłasza aktywny alarm {alarm_code}.",
}


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, content: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(content, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


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


def _entity_entry(
    register: HombeeAirRegister,
    name: str,
    option_states: dict[str, str] | None = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {"name": name}
    if register.options:
        entry["state"] = {
            OPTION_STATE_KEYS[label]: (
                option_states[label] if option_states is not None else label
            )
            for _, label in register.options
        }
    return entry


def _entity_translations(language: str) -> dict[str, dict[str, dict[str, Any]]]:
    entities: dict[str, dict[str, dict[str, Any]]] = {}
    for register in REGISTERS:
        platform = _platform(register)
        if language == "pl":
            name = POLISH_ENTITY_NAMES[register.key]
            if register.alarm_code is not None:
                name = f"{name} ({register.alarm_code})"
            option_states = POLISH_OPTION_STATES
        else:
            name = register.name
            option_states = None
        entities.setdefault(platform, {})[register.key] = _entity_entry(
            register,
            name,
            option_states,
        )
    return entities


def _with_generated_sections(
    content: dict[str, Any],
    language: str,
) -> dict[str, Any]:
    updated = dict(content)
    updated["issues"] = {
        "active_alarm": (
            PL_ACTIVE_ALARM_ISSUE if language == "pl" else EN_ACTIVE_ALARM_ISSUE
        )
    }
    updated["entity"] = _entity_translations(language)
    return {
        key: updated[key]
        for key in ("config", "entity", "issues", "services")
        if key in updated
    }


def main() -> int:
    strings = _load_json(COMPONENT_PATH / "strings.json")
    english = _load_json(TRANSLATIONS_PATH / "en.json")
    polish = _load_json(TRANSLATIONS_PATH / "pl.json")

    _write_json(
        COMPONENT_PATH / "strings.json", _with_generated_sections(strings, "en")
    )
    _write_json(TRANSLATIONS_PATH / "en.json", _with_generated_sections(english, "en"))
    _write_json(TRANSLATIONS_PATH / "pl.json", _with_generated_sections(polish, "pl"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
