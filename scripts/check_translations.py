from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

COMPONENT_PATH = Path("custom_components/hombee_air")
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


def main() -> int:
    strings = _load_json(COMPONENT_PATH / "strings.json")
    baseline_paths = _leaf_paths(strings)

    for language in REQUIRED_LANGUAGES:
        translation_path = TRANSLATIONS_PATH / f"{language}.json"
        translation = _load_json(translation_path)
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

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
