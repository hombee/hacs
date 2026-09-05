"""Daily brightness profiles and a bounded room illuminance controller."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

import voluptuous as vol

ACTIVITIES = ("auto", "reading", "cooking", "relax", "night")
DEFAULT_PROFILE = "default"


def finite_number(value: Any) -> float:
    """Reject NaN and infinity as well as nonnumeric inputs."""
    try:
        result = float(value)
    except (ValueError, TypeError) as error:
        raise vol.Invalid("Expected a finite number") from error
    if not math.isfinite(result):
        raise vol.Invalid("Expected a finite number")
    return result


def clock_time(value: Any) -> str:
    """Store a local wall-clock time in HH:MM format."""
    try:
        hour, minute = str(value).split(":")
        if not (0 <= int(hour) < 24 and 0 <= int(minute) < 60):
            raise ValueError
    except ValueError as error:
        raise vol.Invalid("Expected HH:MM") from error
    return f"{int(hour):02d}:{int(minute):02d}"


def minutes(value: str) -> int:
    """Convert a validated wall-clock time to minutes since midnight."""
    hour, minute = value.split(":")
    return int(hour) * 60 + int(minute)


PERCENT = vol.All(finite_number, vol.Range(min=1, max=100))
LUX = vol.All(finite_number, vol.Range(min=1, max=10000))
PROFILE_SCHEMA = vol.Schema(
    {
        vol.Optional("wake_time", default="07:00"): clock_time,
        vol.Optional("sleep_time", default="23:00"): clock_time,
        vol.Optional("wake_ramp_minutes", default=30): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=240)
        ),
        vol.Optional("wind_down_minutes", default=120): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=480)
        ),
        vol.Optional("day_brightness", default=100): PERCENT,
        vol.Optional("evening_brightness", default=40): PERCENT,
        vol.Optional("night_brightness", default=5): PERCENT,
        vol.Optional("task_brightness", default=100): PERCENT,
        vol.Optional("day_lux", default=300): LUX,
        vol.Optional("evening_lux", default=100): LUX,
        vol.Optional("night_lux", default=10): LUX,
        vol.Optional("task_lux", default=500): LUX,
        vol.Optional("illuminance_sensor", default=""): str,
        vol.Optional("sensor_max_age", default=300): vol.All(
            vol.Coerce(int), vol.Range(min=30, max=3600)
        ),
    }
)


@dataclass(frozen=True, slots=True)
class BrightnessProfile:
    """Room targets; percentages describe lamp commands, lux describe the sensor."""

    wake_time: str = "07:00"
    sleep_time: str = "23:00"
    wake_ramp_minutes: int = 30
    wind_down_minutes: int = 120
    day_brightness: float = 100
    evening_brightness: float = 40
    night_brightness: float = 5
    task_brightness: float = 100
    day_lux: float = 300
    evening_lux: float = 100
    night_lux: float = 10
    task_lux: float = 500
    illuminance_sensor: str = ""
    sensor_max_age: int = 300

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> BrightnessProfile:
        """Validate both UI input and restored storage."""
        profile = cls(**PROFILE_SCHEMA(raw))
        awake = (minutes(profile.sleep_time) - minutes(profile.wake_time)) % 1440
        if awake <= profile.wake_ramp_minutes + profile.wind_down_minutes:
            raise vol.Invalid("Wake and evening ramps must fit between wake and sleep")
        return profile

    def to_dict(self) -> dict[str, Any]:
        """Serialize user-editable settings."""
        return asdict(self)

    def targets(self, now: datetime, activity: str = "auto") -> tuple[float, float]:
        """Calculate targets in HA local time, including schedules across midnight."""
        fixed_targets = {
            "reading": (self.task_brightness, self.task_lux),
            "cooking": (self.task_brightness, self.task_lux),
            "relax": (self.evening_brightness, self.evening_lux),
            "night": (self.night_brightness, self.night_lux),
        }
        if activity in fixed_targets:
            return fixed_targets[activity]
        elapsed = (now.hour * 60 + now.minute - minutes(self.wake_time)) % 1440
        awake = (minutes(self.sleep_time) - minutes(self.wake_time)) % 1440
        night = (self.night_brightness, self.night_lux)
        day = (self.day_brightness, self.day_lux)
        evening = (self.evening_brightness, self.evening_lux)
        if elapsed >= awake:
            return night
        if elapsed < self.wake_ramp_minutes:
            return _blend(night, day, elapsed / self.wake_ramp_minutes)
        remaining = awake - elapsed
        if remaining >= self.wind_down_minutes:
            return day
        phase = 1 - remaining / self.wind_down_minutes
        if phase < 0.5:
            return _blend(day, evening, phase * 2)
        return _blend(evening, night, (phase - 0.5) * 2)


def _blend(
    start: tuple[float, float], end: tuple[float, float], phase: float
) -> tuple[float, float]:
    eased = phase * phase * (3 - 2 * phase)
    return tuple(a + (b - a) * eased for a, b in zip(start, end, strict=True))


@dataclass(slots=True)
class LuxController:
    """One controller per room, advancing only on a new, settled sensor report."""

    filtered_lux: float | None = None
    sample_time: datetime | None = None
    output: float | None = None
    settled_after: datetime | None = None

    def update(
        self,
        *,
        measured: float,
        reported: datetime,
        target: float,
        current: float,
        minimum: float,
        maximum: float,
    ) -> float:
        """Use a deadband and at most five percentage points per fresh sample."""
        if (self.sample_time is not None and reported <= self.sample_time) or (
            self.settled_after is not None and reported <= self.settled_after
        ):
            return current if self.output is None else self.output
        self.sample_time = reported
        self.filtered_lux = (
            measured
            if self.filtered_lux is None
            else 0.3 * measured + 0.7 * self.filtered_lux
        )
        error = target - self.filtered_lux
        correction = (
            0
            if abs(error) <= max(2, target * 0.1)
            else max(-5, min(5, error / target * 10))
        )
        self.output = max(minimum, min(maximum, current + correction))
        return self.output
