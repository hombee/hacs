"""Administrator-only energy protocol on HA's authenticated WebSocket."""

import voluptuous as vol
from homeassistant.components import websocket_api

from ..const import DOMAIN
from .models import KEY, PUBLISHER
from .runtime import EnergyRuntime


async def async_setup_energy(hass):
    """Register independently of optional Hombee feature config entries."""
    runtime = EnergyRuntime(hass)
    await runtime.load()
    hass.data.setdefault(DOMAIN, {})["energy"] = runtime
    for command in (status, configure, save, unconfigure):
        websocket_api.async_register_command(hass, command)


@websocket_api.websocket_command(
    {vol.Required("type"): "hombee/energy/status", vol.Optional("publisherId"): KEY}
)
@websocket_api.require_admin
@websocket_api.async_response
async def status(hass, connection, msg):
    """Discover source choices and inspect token-free current configuration."""
    connection.send_result(
        msg["id"], hass.data[DOMAIN]["energy"].snapshot(msg.get("publisherId"))
    )


COMMON = {
    vol.Required("protocol"): 1,
    vol.Required("instanceId"): KEY,
    vol.Required("publisherId"): KEY,
    vol.Required("operationId"): KEY,
    vol.Required("expectedRevision"): vol.All(int, vol.Range(min=0)),
}


async def _update(hass, connection, msg, operation):
    runtime = hass.data[DOMAIN]["energy"]
    try:
        await runtime.update(msg, operation)
        connection.send_result(msg["id"], runtime.snapshot(msg.get("publisherId")))
    except vol.Invalid as error:
        connection.send_error(msg["id"], "invalid_energy_configuration", str(error))


@websocket_api.websocket_command(
    {
        **COMMON,
        vol.Required("type"): "hombee/energy/configure",
        vol.Required("bindings"): list,
        vol.Required("publisher"): PUBLISHER,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def configure(hass, connection, msg):
    """Install an energy-specific scoped grant and an atomic binding set."""
    await _update(hass, connection, msg, "configure")


@websocket_api.websocket_command(
    {
        **COMMON,
        vol.Required("type"): "hombee/energy/save",
        vol.Required("bindings"): list,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def save(hass, connection, msg):
    """Persist explicit mapping and opt-in policies without changing the grant."""
    await _update(hass, connection, msg, "save")


@websocket_api.websocket_command(
    {
        **COMMON,
        vol.Required("type"): "hombee/energy/unconfigure",
        vol.Required("bindingId"): KEY,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def unconfigure(hass, connection, msg):
    """Remove one binding; final removal deletes the grant and stops delivery."""
    await _update(hass, connection, msg, "unconfigure")
