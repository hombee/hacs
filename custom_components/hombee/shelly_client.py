"""Local, model-independent Shelly Gen2+ RPC transport."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import socket
from dataclasses import dataclass, field
from typing import Any

from aiohttp import ClientError, ClientTimeout, DigestAuthMiddleware
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from yarl import URL

DOCUMENTATION = {
    "api": "https://shelly-api-docs.shelly.cloud/gen2/",
    "device": "https://shelly-api-docs.shelly.cloud/gen2/ComponentsAndServices/Shelly/",
    "rpc": "https://shelly-api-docs.shelly.cloud/gen2/General/RPCChannels/",
}
MAX_PAYLOAD_BYTES = 65536
MAX_RESPONSE_BYTES = 262144
METHOD_PATTERN = r"^[A-Za-z][A-Za-z0-9_]*\.[A-Za-z][A-Za-z0-9_]*$"
_SECRET_FIELDS = {"password", "pass", "token", "ha1", "private_key", "cloud_key"}


class ShellyError(Exception):
    """A bounded, credential-free diagnostic for the WebSocket caller."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def redact(value: Any) -> Any:
    """Keep vendor JSON extensible while withholding credential fields."""
    if isinstance(value, dict):
        return {
            key: "[redacted]" if key.lower() in _SECRET_FIELDS else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def device_id(mac: str) -> str:
    """Use the MAC identity across address changes and HA entry reloads."""
    normalized = mac.replace(":", "").replace("-", "").lower()
    if not re.fullmatch(r"[a-f0-9]{12}", normalized):
        raise ShellyError("shelly_invalid_identity", "Shelly returned an invalid MAC.")
    return f"shelly-{normalized}"


@dataclass
class ShellyTarget:
    """Connection metadata; secrets are resolved from HA only when calling."""

    host: str
    port: int = 80
    entry_id: str | None = None
    info: dict[str, Any] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class ShellyClient:
    """Pin requests to local addresses and verify identity before each operation."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def connection(self, target: ShellyTarget) -> tuple[URL, tuple]:
        """Resolve once and use the validated IP for all requests in an operation."""
        host, port = target.host, target.port
        username, password = "admin", ""
        verify_ssl = True
        if target.entry_id is not None:
            entry = self.hass.config_entries.async_get_entry(target.entry_id)
            if entry is None or entry.domain != "shelly":
                raise ShellyError(
                    "shelly_not_found", "The Shelly HA entry was removed."
                )
            host = entry.data[CONF_HOST]
            port = entry.data.get(CONF_PORT, 80)
            username = entry.data.get(CONF_USERNAME) or "admin"
            password = entry.data.get(CONF_PASSWORD) or ""
            verify_ssl = entry.data.get("verify_ssl", False)
        if not host or any(char in host for char in "/@?#\\"):
            raise ShellyError("shelly_invalid_host", "Provide a local hostname or IP.")
        try:
            async with asyncio.timeout(2):
                addresses = await self.hass.loop.getaddrinfo(
                    host, port, type=socket.SOCK_STREAM
                )
        except (OSError, TimeoutError) as err:
            raise ShellyError(
                "shelly_unavailable", "Cannot resolve the Shelly host."
            ) from err
        if not addresses:
            raise ShellyError("shelly_unavailable", "The Shelly host has no address.")
        for address in addresses:
            ip = ipaddress.ip_address(address[4][0])
            if (
                not ip.is_private
                or ip.is_loopback
                or ip.is_unspecified
                or ip.is_multicast
                or ip.is_reserved
                or ip.is_link_local
            ):
                raise ShellyError(
                    "shelly_invalid_host", "Only local LAN addresses are allowed."
                )
        url = URL.build(
            scheme="https" if port == 443 else "http",
            host=addresses[0][4][0],
            port=port,
        )
        middleware = DigestAuthMiddleware(username, password)
        return url, (middleware, verify_ssl)

    async def request(
        self, connection: tuple[URL, tuple], path: str, payload: dict | None = None
    ) -> Any:
        """Never follow redirects, retry writes, or forward transport exception text."""
        url, (middleware, verify_ssl) = connection
        session = async_get_clientsession(self.hass, verify_ssl=verify_ssl)
        try:
            async with session.request(
                "GET" if payload is None else "POST",
                url.with_path(path),
                json=payload,
                allow_redirects=False,
                timeout=ClientTimeout(total=4),
                middlewares=(middleware,),
            ) as response:
                if response.status == 401:
                    raise ShellyError(
                        "shelly_auth_required",
                        "Configure the Shelly credentials in Home Assistant's "
                        "Shelly integration.",
                    )
                if response.status != 200:
                    raise ShellyError(
                        "shelly_http_error", f"Shelly HTTP status {response.status}."
                    )
                body = bytearray()
                async for chunk in response.content.iter_chunked(16384):
                    body.extend(chunk)
                    if len(body) > MAX_RESPONSE_BYTES:
                        raise ShellyError(
                            "shelly_response_too_large",
                            "Shelly response exceeds 256 KiB.",
                        )
                return json.loads(body)
        except (ClientError, TimeoutError) as err:
            raise ShellyError(
                "shelly_unavailable", "Shelly connection failed or timed out."
            ) from err
        except (ValueError, UnicodeError) as err:
            raise ShellyError(
                "shelly_invalid_response", "Shelly returned invalid JSON."
            ) from err

    async def identify(
        self, connection: tuple[URL, tuple], expected_id: str | None = None
    ) -> dict[str, Any]:
        """The unauthenticated /shelly endpoint supplies generation and identity."""
        info = await self.request(connection, "/shelly")
        if not isinstance(info, dict) or not isinstance(info.get("mac"), str):
            raise ShellyError(
                "shelly_invalid_identity", "The endpoint is not a Shelly device."
            )
        if expected_id is not None and device_id(info["mac"]) != expected_id:
            raise ShellyError(
                "shelly_identity_changed",
                "A different device now uses this address. Discover again.",
            )
        if not isinstance(info.get("gen"), int) or info["gen"] < 2:
            raise ShellyError(
                "shelly_unsupported_generation",
                "This RPC bridge requires Shelly Gen2 or later.",
            )
        return info

    async def rpc(
        self, connection: tuple[URL, tuple], method: str, params: dict
    ) -> dict:
        """Return the vendor result or error without enumerating its schema."""
        payload = {"id": 1, "method": method, "params": params}
        try:
            encoded = json.dumps(payload, allow_nan=False).encode()
        except (ValueError, TypeError, RecursionError) as err:
            raise ShellyError(
                "shelly_invalid_params", "Parameters must be finite JSON values."
            ) from err
        if len(encoded) > MAX_PAYLOAD_BYTES:
            raise ShellyError(
                "shelly_request_too_large", "Shelly request exceeds 64 KiB."
            )
        response = await self.request(connection, "/rpc", payload)
        if (
            not isinstance(response, dict)
            or response.get("id") != 1
            or ("result" in response) == ("error" in response)
        ):
            raise ShellyError(
                "shelly_invalid_response", "Invalid Shelly RPC response envelope."
            )
        value = response.get("result")
        if method == "Shelly.GetDeviceInfo" and isinstance(value, dict):
            value = {key: item for key, item in value.items() if key != "key"}
        return {"result": redact(value), "rpc_error": redact(response.get("error"))}

    async def methods(self, connection: tuple[URL, tuple]) -> list[str]:
        response = await self.rpc(connection, "Shelly.ListMethods", {})
        result = response["result"]
        if (
            response["rpc_error"] is not None
            or not isinstance(result, dict)
            or not isinstance(result.get("methods"), list)
            or not all(isinstance(method, str) for method in result["methods"])
        ):
            raise ShellyError(
                "shelly_method_discovery_failed", "Cannot list Shelly RPC methods."
            )
        return result["methods"]
