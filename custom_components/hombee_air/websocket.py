"""WebSocket API for Hombee managed lights."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv

from .const import (
    DEFAULT_COOL_KELVIN,
    DEFAULT_TRANSITION_SECONDS,
    DEFAULT_WARM_KELVIN,
    DOMAIN,
    WS_LIST_MANAGED_LIGHTS,
    WS_RECONCILE_MANAGED_LIGHTS,
)
from .managed_lighting import (
    ManagedLightingError,
    ManagedLightingManager,
    ManagedLightSpec,
)

_LIGHT_SCHEMA = vol.Schema(
    {
        vol.Required("source_entity_id"): cv.entity_id,
        vol.Optional("warm_kelvin", default=DEFAULT_WARM_KELVIN): vol.All(
            vol.Coerce(int), vol.Range(min=1500, max=10000)
        ),
        vol.Optional("cool_kelvin", default=DEFAULT_COOL_KELVIN): vol.All(
            vol.Coerce(int), vol.Range(min=1500, max=10000)
        ),
        vol.Optional("transition_seconds", default=DEFAULT_TRANSITION_SECONDS): vol.All(
            vol.Coerce(float), vol.Range(min=0, max=300)
        ),
    }
)


def async_register_websocket_commands(hass: HomeAssistant) -> None:
    """Registers managed-light commands once per Home Assistant process."""
    if hass.data.setdefault(DOMAIN, {}).get("websocket_registered"):
        return
    websocket_api.async_register_command(hass, websocket_reconcile_managed_lights)
    websocket_api.async_register_command(hass, websocket_list_managed_lights)
    hass.data[DOMAIN]["websocket_registered"] = True


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_RECONCILE_MANAGED_LIGHTS,
        vol.Required("revision"): vol.All(vol.Coerce(int), vol.Range(min=0)),
        vol.Required("lights"): [_LIGHT_SCHEMA],
    }
)
@websocket_api.async_response
async def websocket_reconcile_managed_lights(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Applies the complete managed-light manifest."""
    manager = _manager(hass)
    if manager is None:
        connection.send_error(
            msg["id"],
            "not_configured",
            "Hombee managed lighting is not configured",
        )
        return
    specs = [ManagedLightSpec(**raw) for raw in msg["lights"]]
    try:
        result = await manager.async_reconcile(msg["revision"], specs)
    except ManagedLightingError as error:
        connection.send_error(msg["id"], "invalid_manifest", str(error))
        return
    connection.send_result(msg["id"], result)


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): WS_LIST_MANAGED_LIGHTS})
def websocket_list_managed_lights(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Returns the active managed-light mappings."""
    manager = _manager(hass)
    connection.send_result(
        msg["id"], manager.as_dict() if manager is not None else None
    )


def _manager(hass: HomeAssistant) -> ManagedLightingManager | None:
    for entry in hass.config_entries.async_entries(DOMAIN):
        runtime = getattr(entry, "runtime_data", None)
        if isinstance(runtime, ManagedLightingManager):
            return runtime
    return None
