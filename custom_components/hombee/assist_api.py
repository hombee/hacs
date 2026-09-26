"""Administrator-only capability discovery and cloud association."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import instance_id
from homeassistant.loader import async_get_integration

from .assist import ASSIST_ENTRY_KIND, ASSIST_PROTOCOL
from .const import CONF_ENTRY_KIND, DOMAIN


@callback
def async_register_assist_api(hass: HomeAssistant) -> None:
    """Expose capabilities even before the voice feature is configured."""
    websocket_api.async_register_command(hass, websocket_assist_status)
    websocket_api.async_register_command(hass, websocket_assist_configure)


@websocket_api.websocket_command({vol.Required("type"): "hombee/assist/status"})
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_assist_status(hass, connection, msg) -> None:
    """Return identity and protocol without exposing the installation token."""
    integration = await async_get_integration(hass, DOMAIN)
    entries = hass.config_entries.async_entries(DOMAIN)
    connection.send_result(
        msg["id"],
        {
            "protocol": ASSIST_PROTOCOL,
            "instanceId": await instance_id.async_get(hass),
            "version": integration.version,
            "configured": any(
                entry.data.get(CONF_ENTRY_KIND) == ASSIST_ENTRY_KIND
                and bool(entry.data.get("token"))
                for entry in entries
            ),
        },
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): "hombee/assist/configure",
        vol.Required("instanceId"): str,
        vol.Required("token"): vol.All(str, vol.Length(min=64, max=128)),
        vol.Required("gatewayUrl"): vol.In(["https://hombee.app"]),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_assist_configure(hass, connection, msg) -> None:
    """Install the account grant only on the instance that was inspected."""
    if msg["instanceId"] != await instance_id.async_get(hass):
        connection.send_error(
            msg["id"], "wrong_instance", "Home Assistant identity changed."
        )
        return
    settings = {key: msg[key] for key in ("instanceId", "token", "gatewayUrl")}
    entry = next(
        (
            entry
            for entry in hass.config_entries.async_entries(DOMAIN)
            if entry.data.get(CONF_ENTRY_KIND) == ASSIST_ENTRY_KIND
        ),
        None,
    )
    if entry is None:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "import"}, data=settings
        )
        if result["type"] != "create_entry":
            connection.send_error(
                msg["id"], "setup_failed", "Could not set up Hombee Voice."
            )
            return
        entry = result["result"]
    else:
        hass.config_entries.async_update_entry(entry, data={**entry.data, **settings})
        await hass.config_entries.async_reload(entry.entry_id)
    if entry.state is not ConfigEntryState.LOADED:
        connection.send_error(
            msg["id"],
            "setup_failed",
            "Hombee Voice could not load. Check Home Assistant logs.",
        )
        return
    connection.send_result(msg["id"], {"configured": True})
