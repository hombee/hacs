from __future__ import annotations

import json
import re
import sys
from pathlib import Path

MANIFEST_PATH = Path("custom_components/hombee_air/manifest.json")
VERSION_PATTERN = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def main() -> int:
    if len(sys.argv) != 2:
        sys.stderr.write("usage: python scripts/set_version.py <semver>\n")
        return 2

    version = sys.argv[1]
    if VERSION_PATTERN.fullmatch(version) is None:
        sys.stderr.write(f"invalid semantic version: {version}\n")
        return 2

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest["version"] = version
    MANIFEST_PATH.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
