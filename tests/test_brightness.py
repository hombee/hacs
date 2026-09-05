"""Brightness schedule boundaries and room feedback behavior."""

from datetime import UTC, datetime, timedelta

import pytest
import voluptuous as vol

from custom_components.hombee.brightness import BrightnessProfile, LuxController
from custom_components.hombee.managed_lighting import ManagedLightMapping


@pytest.mark.parametrize(
    ("hour", "minute", "expected"),
    [
        (6, 59, 5),
        (7, 0, 5),
        (7, 15, 52.5),
        (7, 30, 100),
        (20, 59, 100),
        (21, 0, 100),
        (22, 0, 40),
        (23, 0, 5),
        (0, 0, 5),
    ],
)
def test_daily_schedule_boundaries(hour, minute, expected):
    profile = BrightnessProfile()
    assert (
        profile.targets(datetime(2026, 9, 5, hour, minute, tzinfo=UTC))[0] == expected
    )


@pytest.mark.parametrize("activity", ["reading", "cooking"])
def test_task_activity_stays_bright_at_night(activity):
    profile = BrightnessProfile()
    assert profile.targets(datetime(2026, 9, 5, 23, tzinfo=UTC), activity) == (100, 500)


def test_schedule_supports_sleep_after_midnight():
    profile = BrightnessProfile.from_dict({"wake_time": "10:00", "sleep_time": "02:00"})
    assert profile.targets(datetime(2026, 9, 5, 23, tzinfo=UTC)) == (100, 300)
    assert profile.targets(datetime(2026, 9, 6, 1, tzinfo=UTC)) == (40, 100)
    assert profile.targets(datetime(2026, 9, 6, 2, tzinfo=UTC)) == (5, 10)


@pytest.mark.parametrize(
    "data",
    [
        {"day_brightness": float("nan")},
        {"day_lux": float("inf")},
        {"night_brightness": 0},
        {"day_brightness": 101},
        {"wake_time": "24:00"},
        {"wake_time": "07:60"},
        {"sleep_time": "07:00"},
        {"wake_time": "21:00", "sleep_time": "22:00"},
        {"sensor_max_age": 0},
    ],
)
def test_invalid_profiles_are_rejected(data):
    with pytest.raises(vol.Invalid):
        BrightnessProfile.from_dict(data)


def test_lux_controller_bounds_deadband_and_settling():
    controller = LuxController()
    now = datetime(2026, 9, 5, tzinfo=UTC)
    inputs = {
        "measured": 1000,
        "reported": now,
        "target": 300,
        "current": 50,
        "minimum": 10,
        "maximum": 90,
    }
    assert controller.update(**inputs) == 45
    # Never integrate the same sensor reading twice, even if the light reported back.
    inputs["current"] = 45
    assert controller.update(**inputs) == 45
    controller.settled_after = now + timedelta(seconds=30)
    inputs["reported"] = now + timedelta(seconds=20)
    assert controller.update(**inputs) == 45
    inputs["reported"] = now + timedelta(seconds=60)
    inputs["current"] = 11
    assert controller.update(**inputs) == 10
    controller = LuxController()
    inputs.update(measured=310, current=50)
    assert controller.update(**inputs) == 50
    controller = LuxController()
    inputs.update(measured=0, current=89)
    assert controller.update(**inputs) == 90


def test_old_storage_retains_color_override_and_adds_brightness_defaults():
    mapping = ManagedLightMapping.from_dict(
        {
            "source_registry_id": "source",
            "public_entity_id": "light.test",
            "physical_entity_id": "light.test_physical",
            "logical_unique_id": "test",
            "name": "Test",
            "warm_kelvin": 2200,
            "cool_kelvin": 5000,
            "transition_seconds": 5,
            "manual_override": True,
        }
    )
    assert mapping.manual_override
    assert not mapping.brightness_override
    assert mapping.adapt_brightness
    assert mapping.min_brightness == 1
    assert mapping.max_brightness == 100
