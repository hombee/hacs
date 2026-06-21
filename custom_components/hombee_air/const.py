"""Constants for the Hombee Air integration."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta

DOMAIN = "hombee_air"

CONF_INSTALLATION_ID = "installation_id"

DEFAULT_PORT = 502
MODBUS_UNIT_ID = 1

FAST_SCAN_INTERVAL = timedelta(seconds=10)
SLOW_SCAN_INTERVAL = timedelta(seconds=60)

# Minimum spacing between any two Modbus operations (parity with the
# previous Home Assistant modbus hub configuration).
IO_PACING_SECONDS = 0.03
# The unit does not tolerate frequent writes: hard global write budget.
WRITE_PACING_SECONDS = 1.0

MANUFACTURER = "Hombee"
MODEL = "Air"

# PrgMode (holding register 60) values.
PROGRAM_OFF = 0
PROGRAM_STANDBY = 1
PROGRAM_ECONOMY = 2
PROGRAM_COMFORT = 3
PROGRAM_COMFORT_PLUS = 4
PROGRAM_AUTO = 5
PROGRAM_MANUAL = 6

PRESET_ECONOMY = "economy"
PRESET_COMFORT = "comfort"
PRESET_COMFORT_PLUS = "comfort_plus"
PRESET_MANUAL = "manual"

PRESET_TO_PROGRAM = {
    PRESET_ECONOMY: PROGRAM_ECONOMY,
    PRESET_COMFORT: PROGRAM_COMFORT,
    PRESET_COMFORT_PLUS: PROGRAM_COMFORT_PLUS,
    PRESET_MANUAL: PROGRAM_MANUAL,
}
PROGRAM_TO_PRESET = {value: key for key, value in PRESET_TO_PROGRAM.items()}

KEY_ROOM_TEMPERATURE = "room_temperature"
KEY_ROOM_HUMIDITY = "room_humidity"
KEY_CURRENT_TEMPERATURE_SETPOINT = "current_temperature_setpoint"
KEY_CURRENT_HUMIDITY_SETPOINT = "current_humidity_setpoint"
KEY_CURRENT_FAN_GEAR = "current_fan_gear"
KEY_CURRENT_PROGRAM = "current_program"
KEY_UNIT_STATUS = "unit_status"
KEY_PROGRAM_MODE = "program_mode"
KEY_FANS_RUNNING = "fans_running"
KEY_COOLING_DEMAND = "msk_cool_img"
KEY_HEATING_DEMAND = "msk_heat_img"
KEY_DRYING_DEMAND = "msk_dehum_img"


@dataclass(frozen=True, slots=True)
class PresetSetpoints:
    """Register keys steering one user program."""

    temperature_keys: tuple[str, ...]
    humidity_keys: tuple[str, ...]
    fan_gear_key: str


PRESET_SETPOINTS: dict[str, PresetSetpoints] = {
    PRESET_ECONOMY: PresetSetpoints(
        temperature_keys=(
            "economy_heating_temperature_setpoint",
            "economy_cooling_temperature_setpoint",
        ),
        humidity_keys=(
            "economy_heating_humidity_setpoint",
            "economy_cooling_humidity_setpoint",
        ),
        fan_gear_key="economy_program_fan_gear",
    ),
    PRESET_COMFORT: PresetSetpoints(
        temperature_keys=(
            "comfort_heating_temperature_setpoint",
            "comfort_cooling_temperature_setpoint",
        ),
        humidity_keys=(
            "comfort_heating_humidity_setpoint",
            "comfort_cooling_humidity_setpoint",
        ),
        fan_gear_key="comfort_program_fan_gear",
    ),
    PRESET_COMFORT_PLUS: PresetSetpoints(
        temperature_keys=(
            "comfort_plus_heating_temperature_setpoint",
            "comfort_plus_cooling_temperature_setpoint",
        ),
        humidity_keys=(
            "comfort_plus_heating_humidity_setpoint",
            "comfort_plus_cooling_humidity_setpoint",
        ),
        fan_gear_key="comfort_plus_program_fan_gear",
    ),
    PRESET_MANUAL: PresetSetpoints(
        temperature_keys=("manual_temperature_setpoint",),
        humidity_keys=("manual_humidity_setpoint",),
        fan_gear_key="manual_supply_fan_gear",
    ),
}


def installation_slug(installation_id: str) -> str:
    """Normalizes an installation id the same way the Hombee app does."""
    normalized = re.sub(r"[^a-z0-9]+", "_", installation_id.strip().lower())
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return normalized or "unit"
