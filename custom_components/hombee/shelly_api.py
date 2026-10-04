"""Administrator-only local Shelly discovery, inspection, and generic RPC API."""

from __future__ import annotations

import asyncio
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api, zeroconf
from homeassistant.const import CONF_HOST, CONF_PORT, EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant, callback
from zeroconf import ServiceStateChange
from zeroconf.asyncio import AsyncServiceBrowser

from .const import DOMAIN
from .shelly_client import (
    DOCUMENTATION,
    METHOD_PATTERN,
    ShellyClient,
    ShellyError,
    ShellyTarget,
    device_id,
    redact,
)

_DEVICE_ID = vol.All(str, vol.Match(r"^shelly-[a-f0-9]{12}$"))
_HOST = vol.All(str, vol.Length(min=1, max=253))
_PORT = vol.All(int, vol.Range(min=1, max=65535))
_SERVICE_TYPES = ["_shelly._tcp.local.", "_http._tcp.local."]
_DATA_KEY = "shelly_bridge"
_MAX_DEVICES = 200


class ShellyBridge:
    """Use HA's existing credentials and discover unconfigured LAN devices."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.client = ShellyClient(hass)
        self.targets: dict[str, ShellyTarget] = {}
        self.services: dict[str, str] = {}
        self.browser: AsyncServiceBrowser | None = None
        self.discovery_lock = asyncio.Lock()
        self.discovery_event = asyncio.Event()

    async def start(self) -> None:
        zc = await zeroconf.async_get_async_instance(self.hass)
        self.browser = AsyncServiceBrowser(
            zc.zeroconf, _SERVICE_TYPES, handlers=[self.service_changed]
        )
        self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, self.stop)

    async def stop(self, _event) -> None:
        if self.browser is not None:
            await self.browser.async_cancel()

    @callback
    def service_changed(self, zeroconf, service_type, name, state_change) -> None:
        """Retain only Shelly advertisements, with bounded discovery state."""
        if service_type == "_http._tcp.local." and not name.lower().startswith(
            "shelly"
        ):
            return
        if state_change is ServiceStateChange.Removed:
            self.services.pop(name, None)
        elif len(self.services) < _MAX_DEVICES or name in self.services:
            self.services[name] = service_type
            self.discovery_event.set()

    def configured(self) -> None:
        """Resolve current config entries rather than retaining secrets or runtimes."""
        for entry in self.hass.config_entries.async_entries("shelly"):
            if not entry.unique_id or not entry.data.get(CONF_HOST):
                continue
            try:
                identity = device_id(entry.unique_id)
            except ShellyError:
                continue
            existing = self.targets.get(identity)
            target = existing or ShellyTarget(entry.data[CONF_HOST])
            target.host = entry.data[CONF_HOST]
            target.port = entry.data.get(CONF_PORT, 80)
            target.entry_id = entry.entry_id
            self.targets[identity] = target

    async def discover(self) -> list[dict[str, str]]:
        """Query advertisements only; do not scan the subnet or invoke RPC writes."""
        async with self.discovery_lock:
            self.configured()
            if self.browser is None:
                await self.start()
                try:
                    async with asyncio.timeout(1):
                        await self.discovery_event.wait()
                except TimeoutError:
                    pass
            zc = await zeroconf.async_get_async_instance(self.hass)
            failures: list[dict[str, str]] = []
            semaphore = asyncio.Semaphore(4)

            async def probe(name: str, service_type: str) -> None:
                async with semaphore:
                    info = await zc.async_get_service_info(
                        service_type, name, timeout=1000
                    )
                    if info is None:
                        return
                    addresses = info.parsed_addresses()
                    if not addresses:
                        return
                    target = ShellyTarget(addresses[0], info.port)
                    try:
                        connection = await self.client.connection(target)
                        identity_info = await self.client.identify(connection)
                        identity = device_id(identity_info["mac"])
                        target.info = identity_info
                        if (
                            identity not in self.targets
                            and len(self.targets) < _MAX_DEVICES
                        ):
                            self.targets[identity] = target
                        elif identity in self.targets:
                            current = self.targets[identity]
                            current.info = identity_info
                            if current.entry_id is None:
                                current.host, current.port = target.host, target.port
                    except ShellyError as err:
                        failures.append({"service": name, "code": err.code})

            try:
                async with asyncio.timeout(8):
                    await asyncio.gather(
                        *(
                            probe(name, kind)
                            for name, kind in list(self.services.items())
                        )
                    )
            except TimeoutError:
                failures.append(
                    {"service": "discovery", "code": "shelly_discovery_timeout"}
                )
            return failures

    def target(self, identity: str) -> ShellyTarget:
        self.configured()
        target = self.targets.get(identity)
        if target is None:
            raise ShellyError(
                "shelly_not_found", "List devices or inspect its local host first."
            )
        return target

    def descriptor(self, identity: str, target: ShellyTarget) -> dict[str, Any]:
        entry = (
            self.hass.config_entries.async_get_entry(target.entry_id)
            if target.entry_id
            else None
        )
        info = target.info
        return {
            "device_id": identity,
            "name": entry.title if entry else info.get("id", identity),
            "host": target.host,
            "port": target.port,
            "configured": entry is not None,
            "gen": info.get("gen", entry.data.get("gen") if entry else None),
            "model": info.get("model", entry.data.get("model") if entry else None),
            "firmware": info.get("ver"),
        }

    async def inspect(self, msg: dict) -> dict[str, Any]:
        identity = msg.get("device_id")
        target = (
            self.target(identity)
            if identity
            else ShellyTarget(msg["host"], msg["port"])
        )
        async with target.lock:
            connection = await self.client.connection(target)
            info = await self.client.identify(connection, identity)
            identity = device_id(info["mac"])
            target.info = info
            # Preserve configured credentials when a host is inspected again.
            self.configured()
            configured_target = self.targets.get(identity)
            if (
                configured_target is not None
                and configured_target is not target
                and configured_target.entry_id is not None
            ):
                connection = await self.client.connection(configured_target)
                await self.client.identify(connection, identity)
                target = configured_target
                target.info = info
            elif configured_target is not None and configured_target is not target:
                configured_target.host = target.host
                configured_target.port = target.port
                configured_target.info = info
                target = configured_target
            if identity not in self.targets:
                if len(self.targets) >= _MAX_DEVICES:
                    raise ShellyError(
                        "shelly_device_limit",
                        "The bridge supports at most 200 devices.",
                    )
                self.targets[identity] = target
            methods = await self.client.methods(connection)
            reads = [
                method
                for method in (
                    "Shelly.GetConfig",
                    "Shelly.GetStatus",
                    "Shelly.GetComponents",
                )
                if method in methods
            ]
            results = await asyncio.gather(
                *(
                    self.client.rpc(
                        connection,
                        method,
                        (
                            {"include": ["config", "status"]}
                            if method == "Shelly.GetComponents"
                            else {}
                        ),
                    )
                    for method in reads
                )
            )
            return {
                "api_version": 1,
                "device": self.descriptor(identity, target),
                "device_info": redact(info),
                "methods": methods,
                "reads": dict(zip(reads, results, strict=True)),
                "documentation": DOCUMENTATION,
            }

    async def call(self, identity: str, method: str, params: dict) -> dict:
        target = self.target(identity)
        async with target.lock:
            connection = await self.client.connection(target)
            target.info = await self.client.identify(connection, identity)
            methods = await self.client.methods(connection)
            if method not in methods:
                raise ShellyError(
                    "shelly_method_unavailable",
                    "This device does not advertise the requested method.",
                )
            try:
                result = await self.client.rpc(connection, method, params)
            except ShellyError as err:
                if err.code in {
                    "shelly_unavailable",
                    "shelly_invalid_response",
                    "shelly_response_too_large",
                    "shelly_http_error",
                }:
                    raise ShellyError(
                        "shelly_call_unconfirmed",
                        "The RPC outcome is unknown. Inspect current state before "
                        "any further action. Do not retry automatically.",
                    ) from err
                raise
            return {"api_version": 1, "device_id": identity, "method": method, **result}


async def async_setup_shelly_api(hass: HomeAssistant) -> None:
    """Expose the bridge independently of Hombee feature config entries."""
    bridge = ShellyBridge(hass)
    hass.data.setdefault(DOMAIN, {})[_DATA_KEY] = bridge
    for handler in (websocket_list, websocket_inspect, websocket_call):
        websocket_api.async_register_command(hass, handler)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "hombee/shelly/list",
        vol.Optional("discover", default=True): bool,
        vol.Optional("limit", default=50): vol.All(int, vol.Range(min=1, max=200)),
        vol.Optional("offset", default=0): vol.All(int, vol.Range(min=0, max=100000)),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_list(hass, connection, msg) -> None:
    """Return paginated identities without exposing stored passwords."""
    bridge = hass.data[DOMAIN][_DATA_KEY]
    bridge.configured()
    failures = await bridge.discover() if msg["discover"] else []
    devices = [
        bridge.descriptor(key, target) for key, target in sorted(bridge.targets.items())
    ]
    connection.send_result(
        msg["id"],
        {
            "api_version": 1,
            "devices": devices[msg["offset"] : msg["offset"] + msg["limit"]],
            "total": len(devices),
            "limit": msg["limit"],
            "offset": msg["offset"],
            "discovery_errors": failures,
            "documentation": DOCUMENTATION,
        },
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): "hombee/shelly/inspect",
        vol.Optional("device_id"): _DEVICE_ID,
        vol.Optional("host"): _HOST,
        vol.Optional("port", default=80): _PORT,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_inspect(hass, connection, msg) -> None:
    """Accept one stable identity or an explicitly supplied local host."""
    if ("device_id" in msg) == ("host" in msg):
        connection.send_error(
            msg["id"], "shelly_invalid_target", "Choose device_id or host."
        )
        return
    try:
        async with asyncio.timeout(10):
            result = await hass.data[DOMAIN][_DATA_KEY].inspect(msg)
    except TimeoutError:
        connection.send_error(
            msg["id"], "shelly_unavailable", "Shelly inspection timed out."
        )
        return
    except ShellyError as err:
        connection.send_error(msg["id"], err.code, str(err))
        return
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "hombee/shelly/call",
        vol.Required("device_id"): _DEVICE_ID,
        vol.Required("method"): vol.All(
            str, vol.Length(max=128), vol.Match(METHOD_PATTERN)
        ),
        vol.Optional("params", default=dict): dict,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_call(hass, connection, msg) -> None:
    """Validate only the transport envelope; Shelly validates its own parameters."""
    try:
        async with asyncio.timeout(10):
            result = await hass.data[DOMAIN][_DATA_KEY].call(
                msg["device_id"], msg["method"], msg["params"]
            )
    except TimeoutError:
        connection.send_error(
            msg["id"],
            "shelly_call_unconfirmed",
            "The RPC timed out. Inspect current state; do not retry automatically.",
        )
        return
    except ShellyError as err:
        connection.send_error(msg["id"], err.code, str(err))
        return
    connection.send_result(msg["id"], result)
