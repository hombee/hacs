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
REQUIRED_LANGUAGES = ("en", "pl")


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _leaf_paths(value: Any, prefix: tuple[str, ...] = ()) -> set[tuple[str, ...]]:
    if isinstance(value, dict):
        paths: set[tuple[str, ...]] = set()
        for key, child in value.items():
            paths |= _leaf_paths(child, (*prefix, key))
        return paths
    return {prefix}


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


def _expected_name(register: HombeeAirRegister, language: str) -> str:
    if language != "pl":
        return register.name
    name = POLISH_ENTITY_NAMES[register.key]
    if register.alarm_code is not None:
        return f"{name} ({register.alarm_code})"
    return name


def _validate_catalog_translations(  # noqa: PLR0912
    strings: dict[str, Any],
    translations: dict[str, dict[str, Any]],
) -> list[str]:
    errors: list[str] = []
    register_keys = {register.key for register in REGISTERS}
    if set(POLISH_ENTITY_NAMES) != register_keys:
        missing = sorted(register_keys - set(POLISH_ENTITY_NAMES))
        extra = sorted(set(POLISH_ENTITY_NAMES) - register_keys)
        if missing:
            errors.append("missing Polish entity labels: " + ", ".join(missing))
        if extra:
            errors.append("extra Polish entity labels: " + ", ".join(extra))

    option_labels = {label for register in REGISTERS for _, label in register.options}
    if set(OPTION_STATE_KEYS) != option_labels:
        missing = sorted(option_labels - set(OPTION_STATE_KEYS))
        extra = sorted(set(OPTION_STATE_KEYS) - option_labels)
        if missing:
            errors.append("missing option state keys: " + ", ".join(missing))
        if extra:
            errors.append("extra option state keys: " + ", ".join(extra))

    if set(POLISH_OPTION_STATES) != option_labels:
        missing = sorted(option_labels - set(POLISH_OPTION_STATES))
        extra = sorted(set(POLISH_OPTION_STATES) - option_labels)
        if missing:
            errors.append("missing Polish option labels: " + ", ".join(missing))
        if extra:
            errors.append("extra Polish option labels: " + ", ".join(extra))

    for language, content in {"strings": strings, **translations}.items():
        entity = content.get("entity")
        if not isinstance(entity, dict):
            errors.append(f"{language} is missing entity translations")
            continue
        expected_language = "en" if language == "strings" else language
        for register in REGISTERS:
            platform = _platform(register)
            entry = entity.get(platform, {}).get(register.key)
            if not isinstance(entry, dict):
                errors.append(f"{language} is missing entity.{platform}.{register.key}")
                continue
            expected_name = _expected_name(register, expected_language)
            if entry.get("name") != expected_name:
                errors.append(
                    f"{language} entity.{platform}.{register.key}.name "
                    f"must be {expected_name!r}"
                )
            if register.options:
                state = entry.get("state")
                expected_state = {
                    OPTION_STATE_KEYS[label]: (
                        POLISH_OPTION_STATES[label]
                        if expected_language == "pl"
                        else label
                    )
                    for _, label in register.options
                }
                if state != expected_state:
                    errors.append(
                        f"{language} entity.{platform}.{register.key}.state "
                        "does not match register options"
                    )
            elif "state" in entry:
                errors.append(
                    f"{language} entity.{platform}.{register.key} has unexpected state"
                )
    return errors


def main() -> int:
    strings = _load_json(COMPONENT_PATH / "strings.json")
    baseline_paths = _leaf_paths(strings)
    translations: dict[str, dict[str, Any]] = {}

    for language in REQUIRED_LANGUAGES:
        translation_path = TRANSLATIONS_PATH / f"{language}.json"
        translation = _load_json(translation_path)
        translations[language] = translation
        translation_paths = _leaf_paths(translation)
        missing_paths = sorted(baseline_paths - translation_paths)
        extra_paths = sorted(translation_paths - baseline_paths)

        if missing_paths or extra_paths:
            sys.stderr.write(f"{translation_path} does not match strings.json\n")
            for path in missing_paths:
                sys.stderr.write(f"missing: {'.'.join(path)}\n")
            for path in extra_paths:
                sys.stderr.write(f"extra: {'.'.join(path)}\n")
            return 1

    errors = _validate_catalog_translations(strings, translations)
    if errors:
        sys.stderr.write("\n".join(errors) + "\n")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
