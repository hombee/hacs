"""Read the official integration's registry identities, never names or sockets.

The keys below are the released HA 2026.11 solaredge_modbus entity descriptions.
Meter and battery identities include the reported serial (or official slot
fallback). Renaming an entity does not alter a binding; replacing hardware does.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.loader import IntegrationNotLoaded, async_get_loaded_integration

OFFICIAL_DOMAIN = "solaredge_modbus"


@dataclass
class Component:
    """One official device and the exact known entity description keys."""

    id: str
    name: str
    serial: str | None
    points: dict = field(default_factory=dict)

    def choice(self, kind):
        """Expose capabilities independently; model 713 need not supply power."""
        fields = []
        if kind == "meter" and "ac_power" in self.points:
            fields.append("gridPower")
        if kind == "battery":
            if "dc_power" in self.points:
                fields.append("batteryPower")
            if (
                "state_of_energy" in self.points
                or "storage_state_of_charge" in self.points
            ):
                fields.append("batterySoc")
        result = {"id": self.id, "displayName": self.name, "availableFields": fields}
        if self.serial:
            result["serial"] = self.serial
        return result


@dataclass
class Source:
    """One opaque official config entry and its inverter/attachments."""

    id: str
    name: str
    inverter: Component
    meters: list[Component]
    batteries: list[Component]

    def public(self):
        fields = set()
        if "on_grid" in self.inverter.points:
            fields.add("gridState")
        for meter in self.meters:
            fields.update(meter.choice("meter")["availableFields"])
        for battery in self.batteries:
            fields.update(battery.choice("battery")["availableFields"])
        if "ac_power" in self.inverter.points and self.meters:
            fields.add("loadPower")
        return {
            "id": self.id,
            "displayName": self.name,
            "serials": [self.inverter.serial] if self.inverter.serial else [],
            "availableFields": sorted(fields),
            "meters": [item.choice("meter") for item in self.meters],
            "batteries": [item.choice("battery") for item in self.batteries],
        }


def discover(hass):  # noqa: PLR0912
    """Allow only registered sensors on official-domain config entries/devices."""
    try:
        integration = async_get_loaded_integration(hass, OFFICIAL_DOMAIN)
    except IntegrationNotLoaded:
        return []
    # Historical custom packages use the same domain as HA's new official
    # integration. Registry platform strings alone cannot prove provenance.
    if not integration.is_built_in:
        return []
    entities, devices = er.async_get(hass), dr.async_get(hass)
    sources = []
    for entry in hass.config_entries.async_entries(OFFICIAL_DOMAIN):
        serial = entry.unique_id
        if not serial:
            continue
        inverter = None
        meters, batteries = [], []
        for device in dr.async_entries_for_config_entry(devices, entry.entry_id):
            identity = next(
                (
                    key
                    for domain, key in device.identifiers
                    if domain == OFFICIAL_DOMAIN
                ),
                None,
            )
            if identity is None:
                continue
            component = Component(
                device.id,
                device.name_by_user or device.name or entry.title,
                device.serial_number,
            )
            if identity == serial:
                inverter = component
            elif identity.startswith(f"{serial}_meter_"):
                meters.append(component)
            elif identity.startswith(f"{serial}_battery_"):
                batteries.append(component)
            else:
                continue
            prefix = f"{identity}_"
            for entity in er.async_entries_for_device(entities, device.id):
                if (
                    entity.platform != OFFICIAL_DOMAIN
                    or entity.config_entry_id != entry.entry_id
                    or entity.disabled_by is not None
                    or entity.domain not in ("sensor", "binary_sensor")
                    or not entity.unique_id.startswith(prefix)
                ):
                    continue
                key = entity.unique_id[len(prefix) :]
                if key in (
                    "ac_power",
                    "dc_power",
                    "on_grid",
                    "state_of_energy",
                    "storage_state_of_charge",
                ):
                    component.points[key] = entity.entity_id
        if inverter:
            if "storage_state_of_charge" in inverter.points:
                batteries.append(
                    Component(
                        f"{inverter.id}:storage",
                        "Storage capacity (SoC only)",
                        None,
                        {
                            "storage_state_of_charge": inverter.points[
                                "storage_state_of_charge"
                            ]
                        },
                    )
                )
            sources.append(
                Source(entry.entry_id, entry.title, inverter, meters, batteries)
            )
    return sources
