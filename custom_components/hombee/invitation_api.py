"""Administrator-only invitation provisioning for the Hombee backend."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import instance_id

from .const import DOMAIN
from .invitations import HombeeInvitations, InvitationError

_KEY = f"{DOMAIN}.invitations"
_ID = vol.All(str, vol.Match(r"^[a-f0-9-]{36}$"))
_EMAIL = vol.All(
    str, vol.Length(min=3, max=254), vol.Match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
)


async def async_setup_invitations(hass: HomeAssistant) -> None:
    """Load the ledger and register scoped, administrator-only commands."""
    runtime = HombeeInvitations(hass)
    await runtime.load()
    hass.data[_KEY] = runtime
    for handler in (status, issue, accept, revoke):
        websocket_api.async_register_command(hass, handler)


@websocket_api.websocket_command({vol.Required("type"): "hombee/invitations/status"})
@websocket_api.require_admin
@websocket_api.async_response
async def status(hass, connection, msg) -> None:
    """Expose instance identity and invitation management metadata."""
    connection.send_result(
        msg["id"],
        {
            "instanceId": await instance_id.async_get(hass),
            "invitations": hass.data[_KEY].snapshot(),
        },
    )


async def _apply(hass, connection, msg, operation) -> None:
    if msg["instanceId"] != await instance_id.async_get(hass):
        connection.send_error(
            msg["id"], "wrong_instance", "Home Assistant identity changed."
        )
        return
    try:
        result = await operation(hass.data[_KEY])
    except InvitationError as error:
        connection.send_error(msg["id"], "invitation_unavailable", str(error))
        return
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "hombee/invitations/issue",
        vol.Required("instanceId"): str,
        vol.Required("inviteId"): _ID,
        vol.Required("email"): _EMAIL,
        vol.Required("name"): vol.All(str, vol.Length(min=1, max=100)),
        vol.Required("isAdmin"): cv.boolean,
        vol.Optional("userId"): vol.All(str, vol.Length(min=1, max=64)),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def issue(hass, connection, msg) -> None:
    """Create a bounded invitation after the administrator selects a user."""

    async def run(runtime):
        item = await runtime.issue(connection.user.id, msg)
        return {"inviteId": item["inviteId"], "expiresAt": item["expiresAt"]}

    await _apply(hass, connection, msg, run)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "hombee/invitations/accept",
        vol.Required("instanceId"): str,
        vol.Required("inviteId"): _ID,
        vol.Required("hombeeUserId"): vol.All(str, vol.Length(min=1, max=128)),
        vol.Required("email"): _EMAIL,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def accept(hass, connection, msg) -> None:
    """The backend passes a verified Hombee identity using the inviter's session."""
    await _apply(
        hass, connection, msg, lambda runtime: runtime.accept(connection.user.id, msg)
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): "hombee/invitations/revoke",
        vol.Required("instanceId"): str,
        vol.Required("inviteId"): _ID,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def revoke(hass, connection, msg) -> None:
    """Remove outstanding and already granted invitation access."""

    async def run(runtime):
        await runtime.revoke(msg["inviteId"])
        return {"revoked": True}

    await _apply(hass, connection, msg, run)
