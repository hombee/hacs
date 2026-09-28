"""Doorbell configuration, discovery and stable entity associations."""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import voluptuous as vol
from homeassistant.auth.permissions.const import POLICY_CONTROL, POLICY_READ
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er

KEY = vol.All(str, vol.Length(min=1, max=128))
NAME = vol.All(str, vol.Length(min=1, max=100))
SOURCE = vol.Any(
    vol.Schema(
        {
            vol.Required("kind"): "event",
            vol.Required("entityId"): cv.entity_id,
            vol.Required("eventType"): KEY,
        }
    ),
    vol.Schema(
        {
            vol.Required("kind"): "state",
            vol.Required("entityId"): cv.entity_id,
            vol.Required("fromState"): KEY,
            vol.Required("toState"): KEY,
        }
    ),
    vol.Schema(
        {vol.Required("kind"): "manual", vol.Required("entityId"): cv.entity_id}
    ),
)
ACTION = vol.Any(
    None,
    vol.Schema(
        {
            vol.Required("kind"): vol.In(
                ["unlock", "unlatch", "open", "press", "script"]
            ),
            vol.Required("entityId"): cv.entity_id,
            vol.Required("label"): NAME,
        }
    ),
)
DOORBELL = vol.Schema(
    {
        vol.Required("id"): KEY,
        vol.Required("name"): NAME,
        vol.Required("enabled"): bool,
        vol.Required("source"): SOURCE,
        vol.Required("cameras"): vol.All([cv.entity_id], vol.Length(max=8)),
        vol.Required("action"): ACTION,
    }
)
ACTION_DOMAINS = {
    "unlock": "lock",
    "unlatch": "lock",
    "open": "cover",
    "press": "button",
    "script": "script",
}
ACTION_SERVICES = {
    "unlock": "unlock",
    "unlatch": "open",
    "open": "open_cover",
    "press": "press",
    "script": "turn_on",
}


def visible(user, doorbell: dict[str, Any]) -> bool:
    """Receiving a ring requires access to that entrance's source entity."""
    return user.permissions.check_entity(doorbell["source"]["entityId"], POLICY_READ)


def for_user(user, doorbell: dict[str, Any]) -> dict[str, Any]:
    """Camera and entrance permissions are independent of ring visibility."""
    result = deepcopy(doorbell)
    result["cameras"] = [
        entity
        for entity in result["cameras"]
        if user.permissions.check_entity(entity, POLICY_READ)
    ]
    action = result["action"]
    if action and not user.permissions.check_entity(action["entityId"], POLICY_CONTROL):
        result["action"] = None
    return result


def bind_entities(hass, doorbells: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    """Remember registry identities so a renamed entity keeps its association."""
    registry = er.async_get(hass)
    bindings = {}
    for doorbell in doorbells:
        entities = [doorbell["source"]["entityId"], *doorbell["cameras"]]
        action = doorbell["action"]
        if action:
            if action["entityId"].split(".")[0] != ACTION_DOMAINS[action["kind"]]:
                raise vol.Invalid("Entrance action does not match its entity domain")
            entities.append(action["entityId"])
        if any(not entity.startswith("camera.") for entity in doorbell["cameras"]):
            raise vol.Invalid("Select camera entities")
        source = doorbell["source"]
        expected = {"event": "event", "state": "binary_sensor"}.get(source["kind"])
        if expected and not source["entityId"].startswith(f"{expected}."):
            raise vol.Invalid("Ring source does not match its entity domain")
        for entity_id in entities:
            if hass.states.get(entity_id) is None:
                raise vol.Invalid(f"Entity is unavailable: {entity_id}")
            entry = registry.async_get(entity_id)
            if entry:
                bindings[entity_id] = {
                    "domain": entry.domain,
                    "platform": entry.platform,
                    "uniqueId": entry.unique_id,
                }
    return bindings


def resolve_entities(hass, doorbells, bindings) -> list[dict[str, Any]]:
    """Resolve all roles through the same registry mapping."""
    registry = er.async_get(hass)

    def resolve(entity_id):
        binding = bindings.get(entity_id)
        if not binding:
            return entity_id
        return registry.async_get_entity_id(
            binding["domain"], binding["platform"], binding["uniqueId"]
        )

    result = deepcopy(doorbells)
    for doorbell in result:
        source = resolve(doorbell["source"]["entityId"])
        if source is None:
            doorbell["enabled"] = False
        else:
            doorbell["source"]["entityId"] = source
        doorbell["cameras"] = [
            resolved
            for entity in doorbell["cameras"]
            if (resolved := resolve(entity)) is not None
        ]
        if doorbell["action"]:
            action = resolve(doorbell["action"]["entityId"])
            if action is None:
                doorbell["action"] = None
            else:
                doorbell["action"]["entityId"] = action
    return result


def discover(
    hass, instance_id: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Suggest standard event doorbells and cameras on their HA device."""
    registry = er.async_get(hass)
    states = hass.states.async_all()
    cameras = [state for state in states if state.domain == "camera"]
    doorbells, candidates = [], []
    domains = {"event", "binary_sensor", "camera", "lock", "cover", "button", "script"}
    for state in states:
        if state.domain not in domains:
            continue
        types = list(state.attributes.get("event_types", []))
        candidates.append(
            {"entityId": state.entity_id, "name": state.name, "eventTypes": types}
        )
        if (
            state.domain != "event"
            or state.attributes.get("device_class") != "doorbell"
        ):
            continue
        entry = registry.async_get(state.entity_id)
        identity = f"{entry.platform}:{entry.unique_id}" if entry else state.entity_id
        associated = []
        if entry and entry.device_id:
            associated = [
                camera.entity_id
                for camera in cameras
                if (camera_entry := registry.async_get(camera.entity_id))
                and camera_entry.device_id == entry.device_id
            ]
        doorbells.append(
            {
                "id": str(uuid5(NAMESPACE_URL, f"{instance_id}:{identity}")),
                "name": state.name,
                "enabled": True,
                "source": {
                    "kind": "event",
                    "entityId": state.entity_id,
                    "eventType": (
                        "ring"
                        if "ring" in types
                        else (types[0] if types else "pressed")
                    ),
                },
                "cameras": associated[:8],
                "action": None,
            }
        )
    return doorbells[:100], candidates[:5000]
