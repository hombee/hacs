"""Authenticated, versioned doorbell configuration and control API."""

from __future__ import annotations

from uuid import uuid4

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN
from .doorbell_models import KEY, visible
from .doorbell_runtime import DoorbellRuntime


async def async_setup_doorbells(hass):
    runtime = DoorbellRuntime(hass)
    await runtime.load()
    hass.data.setdefault(DOMAIN, {})["doorbells"] = runtime
    for command in (status, configure, save, open_entrance):
        websocket_api.async_register_command(hass, command)

    async def report(call):
        doorbell = next(
            (
                item
                for item in runtime.doorbells()
                if item["id"] == call.data["doorbellId"]
            ),
            None,
        )
        if doorbell is None or doorbell["source"]["kind"] != "manual":
            raise HomeAssistantError("Choose a doorbell with an automation ring source")
        if call.context.user_id:
            user = await hass.auth.async_get_user(call.context.user_id)
            if user is None or not visible(user, doorbell):
                raise HomeAssistantError("Doorbell access denied")
        await runtime.ring(doorbell["id"], call.data.get("eventId", str(uuid4())))

    hass.services.async_register(
        DOMAIN,
        "report_doorbell_ring",
        report,
        schema=vol.Schema(
            {vol.Required("doorbellId"): KEY, vol.Optional("eventId"): KEY}
        ),
    )


def _runtime(hass):
    return hass.data[DOMAIN]["doorbells"]


@websocket_api.websocket_command({vol.Required("type"): "hombee/doorbells/status"})
@websocket_api.async_response
async def status(hass, connection, msg):
    connection.send_result(msg["id"], _runtime(hass).snapshot(connection.user))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "hombee/doorbells/configure",
        vol.Required("instanceId"): KEY,
        vol.Required("token"): vol.All(str, vol.Length(min=64, max=128)),
        vol.Required("gatewayUrl"): vol.In(["https://hombee.app"]),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def configure(hass, connection, msg):
    runtime = _runtime(hass)
    if runtime.instance_id != msg["instanceId"]:
        connection.send_error(
            msg["id"], "wrong_instance", "Home Assistant identity changed"
        )
        return
    await runtime.configure({key: msg[key] for key in ("token", "gatewayUrl")})
    connection.send_result(msg["id"], {"configured": True})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "hombee/doorbells/save",
        vol.Required("instanceId"): KEY,
        vol.Required("expectedRevision"): int,
        vol.Required("requestId"): KEY,
        vol.Required("doorbells"): list,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def save(hass, connection, msg):
    runtime = _runtime(hass)
    try:
        if runtime.instance_id != msg["instanceId"]:
            raise vol.Invalid("Home Assistant identity changed")
        await runtime.save(msg["expectedRevision"], msg["requestId"], msg["doorbells"])
        connection.send_result(msg["id"], runtime.snapshot(connection.user))
    except (vol.Invalid, HomeAssistantError) as error:
        connection.send_error(msg["id"], "invalid_doorbell_configuration", str(error))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "hombee/doorbells/open",
        vol.Required("instanceId"): KEY,
        vol.Required("doorbellId"): KEY,
        vol.Required("configurationRevision"): int,
        vol.Required("commandId"): KEY,
        vol.Required("expiresAt"): int,
    }
)
@websocket_api.async_response
async def open_entrance(hass, connection, msg):
    runtime = _runtime(hass)
    try:
        if runtime.instance_id != msg["instanceId"]:
            raise HomeAssistantError("Home Assistant identity changed")
        result = await runtime.open(
            connection.user,
            msg["doorbellId"],
            msg["configurationRevision"],
            msg["commandId"],
            msg["expiresAt"],
        )
        connection.send_result(msg["id"], result)
    except HomeAssistantError as error:
        connection.send_error(msg["id"], "entrance_action_rejected", str(error))
