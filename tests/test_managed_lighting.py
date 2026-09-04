"""Pure policy tests for Hombee managed lighting."""

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.hombee_air.managed_lighting import circadian_kelvin


@pytest.mark.parametrize(
    ("above_horizon", "rising", "now_offset", "expected"),
    [
        (False, False, timedelta(hours=1), 2200),
        (True, True, timedelta(0), 2200),
        (True, True, timedelta(hours=3), 3600),
        (True, True, timedelta(hours=6), 5000),
        (True, False, timedelta(hours=9), 3600),
        (True, False, timedelta(hours=12), 2200),
    ],
)
def test_circadian_curve_matches_shared_solar_policy(
    above_horizon: bool,
    rising: bool,
    now_offset: timedelta,
    expected: int,
) -> None:
    sunrise = datetime(2026, 6, 1, 6, tzinfo=UTC)
    noon = sunrise + timedelta(hours=6)
    sunset = noon + timedelta(hours=6)

    assert (
        circadian_kelvin(
            now=sunrise + now_offset,
            above_horizon=above_horizon,
            rising=rising,
            next_rising=sunrise + timedelta(days=1),
            next_noon=noon if rising else noon + timedelta(days=1),
            next_setting=sunset,
            warm_kelvin=2200,
            cool_kelvin=5000,
        )
        == expected
    )
