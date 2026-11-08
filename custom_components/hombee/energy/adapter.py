"""Normalize official entity states with their actual report times.

No last_changed freshness, energy-counter estimates, update_entity calls, or
assumed zeroes. Inverter AC/DC is never relabelled as pure PV: hybrid conversion
and battery coupling make that inference invalid without a proven topology.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass
class Sample:
    """A value remains absent when unavailable, stale, malformed or wrong-unit."""

    value: float | bool | None
    reported: float | None
    quality: str


def read(hass, entity_id, now, max_age, binary=False):
    """Use last_reported, which advances for equal-valued official polls."""
    state = hass.states.get(entity_id) if entity_id else None
    if (
        state is None
        or state.state in ("unknown", "unavailable")
        or state.attributes.get("restored")
    ):
        return Sample(None, None, "unavailable")
    reported = state.last_reported.timestamp()
    if not 0 <= now - reported <= max_age:
        return Sample(None, reported, "stale")
    if binary:
        return Sample(
            {"on": True, "off": False}.get(state.state),
            reported,
            "fresh" if state.state in ("on", "off") else "unavailable",
        )
    try:
        value = float(state.state)
    except TypeError, ValueError:
        return Sample(None, reported, "unavailable")
    unit = state.attributes.get("unit_of_measurement")
    scale = {"W": 1, "kW": 1000, "%": 1}.get(unit)
    if scale is None or not math.isfinite(value):
        return Sample(None, reported, "unavailable")
    return Sample(value * scale, reported, "fresh")


def observation(hass, binding, sources, now=None):  # noqa: PLR0912, PLR0915
    """Build one coherent selected inverter group and retain observation time."""
    now = datetime.now(UTC).timestamp() if now is None else now
    policy = binding["policy"]
    max_age, max_skew = policy["maxObservationAgeSeconds"], policy["maxSkewSeconds"]
    samples = []

    def point(component, key, *, binary=False, percent=False):
        sample = read(hass, component.points.get(key), now, max_age, binary)
        entity = hass.states.get(component.points.get(key, ""))
        if sample.value is not None and not binary:
            unit = entity.attributes.get("unit_of_measurement")
            if (percent and (unit != "%" or not 0 <= sample.value <= 100)) or (
                not percent and unit not in ("W", "kW")
            ):
                sample = Sample(None, sample.reported, "unavailable")
        samples.append(sample)
        return sample.value

    selected = [source for source in sources if source.id in binding["sourceIds"]]
    telemetry = {"site": binding["site"]}
    grid_state = "unknown"
    inverter_values, grid_values = [], []
    for source in selected:
        inverter_values.append(point(source.inverter, "ac_power"))
        if "on_grid" in source.inverter.points:
            grid_values.append(point(source.inverter, "on_grid", binary=True))
    if len(selected) != len(binding["sourceIds"]):
        samples.append(Sample(None, None, "unavailable"))
    if (
        grid_values
        and len(grid_values) == len(selected)
        and all(value is True for value in grid_values)
    ):
        grid_state = "connected"
    elif (
        grid_values
        and len(grid_values) == len(selected)
        and all(value is False for value in grid_values)
    ):
        grid_state = "disconnected"
    meters = [
        meter
        for source in selected
        for meter in source.meters
        if meter.id == binding.get("gridMeterId")
    ]
    if binding.get("gridMeterId"):
        if len(meters) == 1:
            value = point(meters[0], "ac_power")
            if value is not None:
                # SunSpec meter power is export-positive. Hombee uses import-positive.
                telemetry["gridPower"] = -value
        else:
            samples.append(Sample(None, None, "unavailable"))
    batteries = [
        battery
        for source in selected
        for battery in source.batteries
        if battery.id in binding["batteryIds"]
    ]
    if len(batteries) != len(binding["batteryIds"]):
        samples.append(Sample(None, None, "unavailable"))
    powers = []
    for battery in batteries:
        if "dc_power" in battery.points:
            powers.append(point(battery, "dc_power"))
        if len(batteries) == 1:
            key = (
                "state_of_energy"
                if "state_of_energy" in battery.points
                else "storage_state_of_charge"
            )
            soc = point(battery, key, percent=True)
            if soc is not None:
                telemetry["batterySoc"] = soc
    if (
        powers
        and len(powers) == len(batteries)
        and all(value is not None for value in powers)
    ):
        # Official SolarEdge DC power is negative while charging.
        telemetry["batteryPower"] = -sum(powers)
    times = [sample.reported for sample in samples if sample.reported is not None]
    coherent = bool(times) and max(times) - min(times) <= max_skew
    if (
        coherent
        and inverter_values
        and all(value is not None for value in inverter_values)
        and "gridPower" in telemetry
    ):
        load = sum(inverter_values) + telemetry["gridPower"]
        if load >= 0:
            telemetry["loadPower"] = load
    quality = "fresh"
    if not samples or any(sample.quality == "unavailable" for sample in samples):
        quality = "unavailable"
    elif not coherent or any(sample.quality == "stale" for sample in samples):
        quality = "stale"
    # A mixed sample group cannot be used as if it were a single instant.
    if not coherent:
        telemetry = {"site": binding["site"]}
        grid_state = "unknown"
    observed = min(times) if times else now
    telemetry["timestamp"] = datetime.fromtimestamp(observed, UTC).isoformat()
    return {
        "bindingId": binding["bindingId"],
        "site": binding["site"],
        "sourceId": binding["sourceIds"][0],
        "observedAtMs": int(observed * 1000),
        "quality": quality,
        "gridState": grid_state,
        "telemetry": telemetry,
    }
