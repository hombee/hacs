#!/usr/bin/env bash
set -euo pipefail

image="${HA_DOCKER_IMAGE:-ghcr.io/home-assistant/home-assistant:stable}"

docker run --rm \
  -e PYTHONPATH=/config \
  -v "$PWD/custom_components:/config/custom_components:ro" \
  "$image" \
  sh -euc '
    python -m pip install --no-cache-dir "pymodbus>=3.15,<4" >/tmp/hombee-pip-install.log
    python - <<'"'"'PY'"'"'
import importlib

modules = [
    "custom_components.hombee_air",
    "custom_components.hombee_air.binary_sensor",
    "custom_components.hombee_air.climate",
    "custom_components.hombee_air.config_flow",
    "custom_components.hombee_air.coordinator",
    "custom_components.hombee_air.light",
    "custom_components.hombee_air.managed_lighting",
    "custom_components.hombee_air.modbus_client",
    "custom_components.hombee_air.number",
    "custom_components.hombee_air.select",
    "custom_components.hombee_air.sensor",
    "custom_components.hombee_air.switch",
    "custom_components.hombee_air.websocket",
]

for module in modules:
    importlib.import_module(module)

from custom_components.hombee_air.const import DOMAIN

assert DOMAIN == "hombee_air"
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
cp -R custom_components/hombee_air "$contract_dir/custom_components/"
cp -R \
  tests/ha_docker/custom_components/hombee_managed_light_test \
  "$contract_dir/custom_components/"
cp tests/ha_docker/configuration.yaml "$contract_dir/configuration.yaml"

if ! docker run --rm \
  -v "$contract_dir:/config" \
  "$image" \
  sh -euc '
    python -m pip install --no-cache-dir "pymodbus>=3.11,<4" >/tmp/hombee-pip-install.log
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
