"""Strict protocol-one configuration; no arbitrary service or register writes."""

from __future__ import annotations

import math

import voluptuous as vol
from homeassistant.helpers import config_validation as cv

KEY = vol.All(str, vol.Length(min=1, max=128))
FIELDS = (
    "solarPower",
    "gridPower",
    "loadPower",
    "batteryPower",
    "batterySoc",
    "gridState",
)


def finite(value):
    """Reject booleans, infinities, and NaN before persistence or comparison."""
    if (
        isinstance(value, bool)
        or not isinstance(value, (float, int))
        or not math.isfinite(value)
    ):
        raise vol.Invalid("A finite number is required")
    return value


def condition(value):
    """A numeric range must have at least one bound."""
    value = vol.Schema(
        {
            vol.Required("field"): vol.In(FIELDS),
            vol.Optional("minimum"): finite,
            vol.Optional("maximum"): finite,
            vol.Optional("hysteresis", default=0): vol.All(finite, vol.Range(min=0)),
        }
    )(value)
    if "minimum" not in value and "maximum" not in value:
        raise vol.Invalid("A condition needs a minimum or maximum")
    if value.get("minimum", -math.inf) > value.get("maximum", math.inf):
        raise vol.Invalid("Condition minimum exceeds maximum")
    return value


ACTION = vol.Schema(
    {
        vol.Required("entityId"): vol.All(cv.entity_id, vol.Match(r"^switch\.")),
        vol.Required("service"): vol.In(("turnOn", "turnOff")),
    }
)
RULE = vol.Schema(
    {
        vol.Required("id"): KEY,
        vol.Optional("enabled", default=True): bool,
        vol.Required("conditions"): vol.All([condition], vol.Length(min=1, max=6)),
        vol.Required("dwellSeconds"): vol.All(int, vol.Range(min=0, max=3600)),
        vol.Required("action"): ACTION,
        vol.Optional("manualOverrideUntilMs"): vol.All(int, vol.Range(min=0)),
    }
)
POLICY = vol.Schema(
    {
        vol.Optional("enabled", default=False): bool,
        vol.Optional("maxObservationAgeSeconds", default=30): vol.All(
            int, vol.Range(min=1, max=300)
        ),
        vol.Optional("maxSkewSeconds", default=5): vol.All(
            int, vol.Range(min=0, max=60)
        ),
        vol.Optional("rules", default=list): vol.All([RULE], vol.Length(max=16)),
    }
)
SITE = vol.Schema({vol.Required("provider"): KEY, vol.Required("id"): KEY})
BINDING = vol.Schema(
    {
        vol.Required("bindingId"): KEY,
        vol.Required("site"): SITE,
        vol.Required("sourceIds"): vol.All([KEY], vol.Length(min=1, max=32)),
        vol.Optional("gridMeterId"): KEY,
        vol.Optional("batteryIds", default=list): vol.All([KEY], vol.Length(max=32)),
        vol.Optional("policy", default=dict): POLICY,
    }
)
PUBLISHER = vol.Schema(
    {
        vol.Required("url"): vol.In(("https://hombee.app",)),
        vol.Required("token"): vol.All(str, vol.Length(min=64, max=128)),
        vol.Required("generation"): KEY,
        vol.Required("bindingRevision"): vol.All(int, vol.Range(min=0)),
    }
)
