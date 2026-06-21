#!/usr/bin/env bash
set -euo pipefail

image="${HA_DOCKER_IMAGE:-ghcr.io/home-assistant/home-assistant:stable}"

docker run --rm \
  -e PYTHONPATH=/config \
  -v "$PWD/custom_components:/config/custom_components:ro" \
  "$image" \
  sh -euc '
    python -m pip install --no-cache-dir "pymodbus>=3.11,<4" >/tmp/hombee-pip-install.log
    python - <<'"'"'PY'"'"'
import importlib

modules = [
    "custom_components.hombee_air",
    "custom_components.hombee_air.binary_sensor",
    "custom_components.hombee_air.climate",
    "custom_components.hombee_air.config_flow",
    "custom_components.hombee_air.coordinator",
    "custom_components.hombee_air.modbus_client",
    "custom_components.hombee_air.number",
    "custom_components.hombee_air.select",
    "custom_components.hombee_air.sensor",
    "custom_components.hombee_air.switch",
]

for module in modules:
    importlib.import_module(module)

from custom_components.hombee_air.const import DOMAIN

assert DOMAIN == "hombee_air"
print("Home Assistant Docker import smoke passed")
PY
  '
