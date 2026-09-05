#!/usr/bin/env bash
set -euo pipefail

image="${HA_DOCKER_IMAGE:-ghcr.io/home-assistant/home-assistant:stable}"

docker run --rm --pull always \
  -e PYTHONPATH=/config \
  -v "$PWD/custom_components:/config/custom_components:ro" \
  "$image" \
  sh -euc '
    python -m pip install --no-cache-dir "pymodbus>=3.11" >/tmp/hombee-pip-install.log
    python - <<'"'"'PY'"'"'
import importlib

modules = [
    "custom_components.hombee",
    "custom_components.hombee.binary_sensor",
    "custom_components.hombee.climate",
    "custom_components.hombee.config_flow",
    "custom_components.hombee.coordinator",
    "custom_components.hombee.light",
    "custom_components.hombee.managed_lighting",
    "custom_components.hombee.modbus_client",
    "custom_components.hombee.number",
    "custom_components.hombee.select",
    "custom_components.hombee.sensor",
    "custom_components.hombee.switch",
]

for module in modules:
    importlib.import_module(module)

from custom_components.hombee.const import DOMAIN

assert DOMAIN == "hombee"
print("Home Assistant Docker import smoke passed")
PY
  '

contract_dir="$(mktemp -d "${PWD}/.ha-docker.XXXXXX")"
cleanup() {
  docker run --rm \
    -v "$contract_dir:/config" \
    "$image" \
    chown -R "$(id -u):$(id -g)" /config >/dev/null 2>&1 || true
  rm -rf "$contract_dir"
}
trap cleanup EXIT
mkdir -p "$contract_dir/custom_components"
cp -R custom_components/hombee "$contract_dir/custom_components/"
cp -R \
  tests/ha_docker/custom_components/hombee_managed_light_test \
  "$contract_dir/custom_components/"
cp tests/ha_docker/configuration.yaml "$contract_dir/configuration.yaml"

if ! docker run --rm \
  -v "$contract_dir:/config" \
  "$image" \
  sh -euc '
    python -m pip install --no-cache-dir "pymodbus>=3.11" >/tmp/hombee-pip-install.log
    python -m homeassistant --config /config
  ' >"$contract_dir/home-assistant.log" 2>&1; then
  tail -n 200 "$contract_dir/home-assistant.log"
  exit 1
fi

if ! python3 -c '
import json
from pathlib import Path

result = json.loads(Path("'"$contract_dir"'/managed-light-result.json").read_text())
if result.get("status") != "passed":
    raise SystemExit(json.dumps(result, indent=2))
'; then
  tail -n 200 "$contract_dir/home-assistant.log"
  exit 1
fi

echo "Home Assistant Docker managed-light contract passed"
