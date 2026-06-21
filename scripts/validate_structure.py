from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path()
COMPONENT_PATH = ROOT / "custom_components" / "hombee_air"
MANIFEST_PATH = COMPONENT_PATH / "manifest.json"
IGNORED_PARTS = {".git", ".venv", "node_modules"}
VERSION_PATTERN = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def _format_paths(paths: list[Path]) -> str:
    return ", ".join(map(str, paths))


def _is_repo_artifact(path: Path) -> bool:
    return not IGNORED_PARTS.isdisjoint(path.parts)


def main() -> int:
    errors: list[str] = []
    required_paths = [
        ROOT / "README.md",
        ROOT / "hacs.json",
        ROOT / "SECURITY.md",
        MANIFEST_PATH,
        COMPONENT_PATH / "brand" / "icon.png",
        COMPONENT_PATH / "strings.json",
        COMPONENT_PATH / "translations" / "en.json",
        COMPONENT_PATH / "translations" / "pl.json",
    ]
    missing_paths = [path for path in required_paths if not path.exists()]
    if missing_paths:
        errors.append("missing required files: " + _format_paths(missing_paths))

    forbidden_paths = [
        path
        for path in ROOT.rglob("*")
        if not _is_repo_artifact(path)
        and (path.name in {"__pycache__", ".pytest_cache"} or path.suffix == ".pyc")
    ]
    if forbidden_paths:
        errors.append("forbidden cache files: " + _format_paths(forbidden_paths))

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    required_manifest_keys = {
        "codeowners",
        "config_flow",
        "documentation",
        "domain",
        "integration_type",
        "iot_class",
        "issue_tracker",
        "name",
        "requirements",
        "version",
    }
    missing_keys = sorted(required_manifest_keys - manifest.keys())
    if missing_keys:
        errors.append("manifest is missing keys: " + ", ".join(missing_keys))
    expected_manifest_order = [
        "domain",
        "name",
        *sorted(key for key in manifest if key not in {"domain", "name"}),
    ]
    if list(manifest) != expected_manifest_order:
        errors.append(
            "manifest keys must be ordered as domain, name, then alphabetical order"
        )

    if manifest["domain"] != "hombee_air":
        errors.append("manifest domain must be hombee_air")
    if manifest["documentation"] != "https://github.com/hombee/hacs":
        errors.append("manifest documentation must point at hombee/hacs")
    if manifest["issue_tracker"] != "https://github.com/hombee/hacs/issues":
        errors.append("manifest issue tracker must point at hombee/hacs")
    if VERSION_PATTERN.fullmatch(manifest["version"]) is None:
        errors.append("manifest version must be semantic version x.y.z")
    if "pymodbus>=3.11,<4" not in manifest["requirements"]:
        errors.append("manifest must pin pymodbus with an upper bound")

    hacs = json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))
    if hacs.get("name") != "Hombee Air":
        errors.append("hacs.json name must be Hombee Air")
    if "render_readme" in hacs:
        errors.append("hacs.json must not use unsupported render_readme")

    if errors:
        sys.stderr.write("\n".join(errors) + "\n")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
