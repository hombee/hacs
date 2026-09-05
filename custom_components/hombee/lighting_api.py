"""Versioned, authenticated lighting configuration API for Hombee MCP clients."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .brightness import ACTIVITIES, PROFILE_SCHEMA, finite_number
from .const import DOMAIN
from .lighting_configuration import (
    LIGHT_SETTINGS_SCHEMA,
    LightingConfigurationConflictError,
    configuration_revision,
    configuration_state,
)
from .managed_lighting import ManagedLightingManager

API_VERSION = 1
PROFILE_PATCH_SCHEMA = vol.All(
    vol.Schema(
        {vol.Optional(str(key)): value for key, value in PROFILE_SCHEMA.schema.items()}
    ),
    vol.Length(min=1),
)
LIGHT_PATCH_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Optional(str(key)): value
            for key, value in LIGHT_SETTINGS_SCHEMA.schema.items()
        }
    ),
    vol.Length(min=1),
)
_KEY = vol.All(str, vol.Length(min=1, max=255))
_OPERATIONS = {
    "profile": vol.Schema(
        {
            vol.Required("profile_key"): _KEY,
            vol.Required("settings"): PROFILE_PATCH_SCHEMA,
        }
    ),
    "reset_profile": vol.Schema({vol.Required("profile_key"): _KEY}),
    "light": vol.Schema(
        {
            vol.Required("entity_id"): cv.entity_id,
            vol.Required("settings"): LIGHT_PATCH_SCHEMA,
        }
    ),
    "activity": vol.Schema(
        {
            vol.Required("profile_key"): _KEY,
            vol.Required("activity"): vol.In((*ACTIVITIES, "inherit")),
        }
    ),
    "enabled": vol.All(
        vol.Schema(
            {
                vol.Optional("brightness_enabled"): bool,
                vol.Optional("color_enabled"): bool,
            }
        ),
        vol.Length(min=1),
    ),
    "resume": vol.Schema(
        {
            vol.Required("entity_ids"): vol.All(
                [cv.entity_id], vol.Length(min=1, max=100)
            ),
            vol.Optional("brightness", default=True): bool,
            vol.Optional("color", default=True): bool,
        }
    ),
}


@callback
def async_register_lighting_api(hass: HomeAssistant) -> None:
    """Handlers resolve the current entry on each request, including after reload."""
    websocket_api.async_register_command(hass, websocket_get_lighting)
    websocket_api.async_register_command(hass, websocket_update_lighting)


def _manager(hass: HomeAssistant) -> ManagedLightingManager | None:
    for entry in hass.config_entries.async_entries(DOMAIN):
        runtime = getattr(entry, "runtime_data", None)
        # Unloaded config entries may retain their old runtime_data.
        if (
            isinstance(runtime, ManagedLightingManager)
            and entry.state is ConfigEntryState.LOADED
        ):
            return runtime
    return None


@websocket_api.websocket_command(
    {
        vol.Required("type"): "hombee/lighting/get",
        vol.Optional("limit", default=200): vol.All(int, vol.Range(min=1, max=500)),
        vol.Optional("offset", default=0): vol.All(int, vol.Range(min=0, max=100000)),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_get_lighting(hass, connection, msg) -> None:
    """Read configuration and bounded diagnostics without commanding any lamp."""
    manager = _manager(hass)
    if manager is None:
        connection.send_error(
            msg["id"],
            "hombee_lighting_not_loaded",
            "Enable Hombee managed lighting first.",
        )
        return
    async with manager.configuration_lock:
        connection.send_result(
            msg["id"],
            lighting_snapshot(manager, limit=msg["limit"], offset=msg["offset"]),
        )


@websocket_api.websocket_command(
    {
        vol.Required("type"): "hombee/lighting/update",
        vol.Required("expected_revision"): vol.All(str, vol.Match(r"^[a-f0-9]{64}$")),
        vol.Required("operation"): vol.In(_OPERATIONS),
        vol.Required("data"): dict,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_update_lighting(hass, connection, msg) -> None:
    """Apply one validated edit; stale requests never overwrite newer settings."""
    manager = _manager(hass)
    if manager is None:
        connection.send_error(
            msg["id"],
            "hombee_lighting_not_loaded",
            "Enable Hombee managed lighting first.",
        )
        return
    try:
        data = _OPERATIONS[msg["operation"]](msg["data"])
        await _apply(manager, msg["operation"], data, msg["expected_revision"])
    except LightingConfigurationConflictError as error:
        connection.send_error(msg["id"], "hombee_lighting_conflict", str(error))
    except (vol.Invalid, KeyError) as error:
        connection.send_error(msg["id"], "hombee_lighting_invalid", str(error))
    except OSError:
        connection.send_error(
            msg["id"],
            "hombee_lighting_storage_failed",
            "Lighting settings could not be saved.",
        )
    else:
        connection.send_result(msg["id"], lighting_snapshot(manager))


async def _apply(manager, operation: str, data: dict[str, Any], revision: str) -> None:
    if operation == "profile":
        await manager.async_configure_profile(
            data["profile_key"],
            data["settings"],
            patch=True,
            expected_revision=revision,
        )
    elif operation == "reset_profile":
        await manager.async_reset_profile(
            data["profile_key"], expected_revision=revision
        )
    elif operation == "light":
        mapping = manager._mapping_for_requested_entity(data["entity_id"])
        if mapping is None or mapping.public_entity_id != data["entity_id"]:
            raise vol.Invalid("Choose a managed public light entity")
        await manager.async_configure_light(
            mapping.source_registry_id, data["settings"], expected_revision=revision
        )
    elif operation == "activity":
        await manager.async_set_activity(
            data["profile_key"], data["activity"], expected_revision=revision
        )
    elif operation == "enabled":
        await manager.async_configure_enabled(**data, expected_revision=revision)
    elif operation == "resume":
        if not data["brightness"] and not data["color"]:
            raise vol.Invalid("Choose at least one attribute to resume")
        await manager.async_resume_lights(**data, expected_revision=revision)


def lighting_snapshot(
    manager: ManagedLightingManager, *, limit: int = 200, offset: int = 0
) -> dict[str, Any]:
    """Expose settings, capabilities, sensor choices and the current decision."""
    hass = manager.hass
    configuration = configuration_state(manager)
    areas = sorted(
        (
            {"area_id": area.id, "name": area.name}
            for area in ar.async_get(hass).async_list_areas()
        ),
        key=lambda value: value["area_id"],
    )
    lights = []
    for mapping in sorted(
        manager.mappings.values(), key=lambda item: item.public_entity_id
    ):
        entity = manager.entities.get(mapping.source_registry_id)
        lights.append(
            {
                "entity_id": mapping.public_entity_id,
                "name": mapping.name,
                "area_id": configuration["lights"][mapping.public_entity_id]["area_id"],
                "available": bool(entity and entity.available),
                "is_on": bool(entity and entity.is_on),
                "supports_color_temperature": bool(
                    entity and "color_temp" in entity.supported_color_modes
                ),
                "brightness": entity.brightness if entity else None,
                **manager.brightness_status(mapping),
            }
        )
    sensors = []
    registry = er.async_get(hass)
    for state in hass.states.async_all("sensor"):
        if state.attributes.get("device_class") != "illuminance":
            continue
        entry = registry.async_get(state.entity_id)
        if entry and entry.disabled_by is not None:
            continue
        area_id = entry.area_id if entry else None
        if area_id is None and entry and entry.device_id:
            device = dr.async_get(hass).async_get(entry.device_id)
            area_id = device.area_id if device else None
        try:
            value = finite_number(state.state)
        except vol.Invalid:
            value = None
        sensors.append(
            {
                "entity_id": state.entity_id,
                "area_id": area_id,
                "name": state.name,
                "value": value,
                "unit": state.attributes.get("unit_of_measurement"),
                "last_reported": state.last_reported.isoformat(),
            }
        )
    sensors.sort(key=lambda value: value["entity_id"])
    return {
        "api_version": API_VERSION,
        "revision": configuration_revision(manager),
        "configuration": configuration,
        "areas": areas[offset : offset + limit],
        "lights": lights[offset : offset + limit],
        "sensors": sensors[offset : offset + limit],
        "offset": offset,
        "limit": limit,
        "totals": {"areas": len(areas), "lights": len(lights), "sensors": len(sensors)},
    }
