"""Verify bounded local HTTP, authentication, identity, and RPC envelopes."""

import hashlib
import re
import socket
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import DigestAuthMiddleware, web

from custom_components.hombee.shelly_client import (
    MAX_RESPONSE_BYTES,
    ShellyClient,
    ShellyError,
    ShellyTarget,
)

INFO = {"mac": "AABBCCDDEEFF", "gen": 3, "model": "future", "ver": "2.0"}


@pytest.mark.parametrize(
    "host", ["8.8.8.8", "127.0.0.1", "::1", "::", "224.0.0.1", "169.254.169.254"]
)
async def test_refuses_non_lan_destinations(hass, host):
    addresses = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (host, 80))]
    with (
        patch.object(hass.loop, "getaddrinfo", AsyncMock(return_value=addresses)),
        pytest.raises(ShellyError),
    ):
        await ShellyClient(hass).connection(ShellyTarget(host))


@pytest.mark.parametrize(
    "host",
    ["http://192.168.1.5", "user@host", "host/path", "host?x=1", "host#fragment"],
)
async def test_refuses_urls_and_userinfo(hass, host):
    with pytest.raises(ShellyError, match="local hostname"):
        await ShellyClient(hass).connection(ShellyTarget(host))


async def test_pins_resolved_ip_and_rejects_mixed_public_resolution(hass):
    addresses = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.50", 80))]
    with patch.object(hass.loop, "getaddrinfo", AsyncMock(return_value=addresses)):
        url, _ = await ShellyClient(hass).connection(ShellyTarget("shelly.local"))
        assert url.host == "192.168.1.50"
        addresses.append((socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 80)))
        with pytest.raises(ShellyError):
            await ShellyClient(hass).connection(ShellyTarget("shelly.local"))


async def test_identity_and_generation_checked(hass):
    client = ShellyClient(hass)
    client.request = AsyncMock(return_value=INFO)
    assert await client.identify(None, "shelly-aabbccddeeff") == INFO
    with pytest.raises(ShellyError, match="different device"):
        await client.identify(None, "shelly-112233445566")
    client.request.return_value = {"mac": INFO["mac"], "type": "SHSW-1"}
    with pytest.raises(ShellyError, match="Gen2"):
        await client.identify(None)


async def test_rpc_accepts_future_payloads_and_redacts_secrets(hass):
    client = ShellyClient(hass)
    client.request = AsyncMock(
        return_value={
            "id": 1,
            "result": {
                "new": [None, {"password": "secret", "key": "switch:0"}],
                "token": "secret",
            },
        }
    )
    result = await client.rpc(None, "Future.SetConfig", {"future": [False, None, 1.5]})
    assert "secret" not in str(result)
    assert result["result"]["new"][1]["key"] == "switch:0"
    client.request.assert_awaited_once_with(
        None,
        "/rpc",
        {
            "id": 1,
            "method": "Future.SetConfig",
            "params": {"future": [False, None, 1.5]},
        },
    )
    client.request.return_value = {
        "id": 1,
        "result": {"key": "cloud-jwt", "mac": INFO["mac"]},
    }
    assert (
        "key"
        not in (await client.rpc(None, "Shelly.GetDeviceInfo", {"ident": True}))[
            "result"
        ]
    )


@pytest.mark.parametrize(
    "response",
    [{"id": 2, "result": {}}, {"id": 1}, {"id": 1, "result": {}, "error": {}}, []],
)
async def test_rejects_invalid_rpc_envelopes(hass, response):
    client = ShellyClient(hass)
    client.request = AsyncMock(return_value=response)
    with pytest.raises(ShellyError, match="envelope"):
        await client.rpc(None, "Shelly.GetStatus", {})


@pytest.mark.parametrize(
    "params", [{"data": "x" * 65536}, {"value": float("nan")}, {"value": float("inf")}]
)
async def test_rejects_oversize_or_non_json_before_network(hass, params):
    client = ShellyClient(hass)
    client.request = AsyncMock()
    with pytest.raises(ShellyError):
        await client.rpc(None, "Future.Set", params)
    client.request.assert_not_called()


async def test_digest_sha256_handshake(hass, aiohttp_server, socket_enabled):
    calls = []

    async def handler(request):
        calls.append(await request.json())
        authorization = request.headers.get("Authorization", "")
        if not authorization:
            return web.Response(
                status=401,
                headers={
                    "WWW-Authenticate": 'Digest realm="shelly-test", '
                    'nonce="nonce-1", algorithm=SHA-256, qop="auth"'
                },
            )
        values = {
            match[0]: match[1] or match[2]
            for match in re.findall(r'(\w+)=(?:"([^"]*)"|([^, ]+))', authorization)
        }

        def digest(value):
            return hashlib.sha256(value.encode()).hexdigest()

        ha1 = digest("admin:shelly-test:local-password")
        ha2 = digest(f"POST:{values['uri']}")
        expected = digest(f"{ha1}:nonce-1:{values['nc']}:{values['cnonce']}:auth:{ha2}")
        assert values["response"] == expected
        return web.json_response({"id": 1, "result": {"new": [None, True]}})

    app = web.Application()
    app.router.add_post("/rpc", handler)
    server = await aiohttp_server(app)
    connection = (
        server.make_url("/"),
        (DigestAuthMiddleware("admin", "local-password"), True),
    )
    result = await ShellyClient(hass).rpc(connection, "Future.Set", {"new": 12})
    assert result == {"result": {"new": [None, True]}, "rpc_error": None}
    assert len(calls) == 2  # Only the authentication challenge is resubmitted.
    assert calls[0] == calls[1]


async def test_http_no_redirects_and_bounded_response(
    hass, aiohttp_server, socket_enabled
):
    async def redirect(_request):
        raise web.HTTPFound("http://example.test/secret")

    async def too_large(_request):
        return web.Response(body=b"x" * (MAX_RESPONSE_BYTES + 1))

    app = web.Application()
    app.router.add_get("/redirect", redirect)
    app.router.add_get("/large", too_large)
    server = await aiohttp_server(app)
    connection = (server.make_url("/"), (DigestAuthMiddleware("admin", ""), True))
    client = ShellyClient(hass)
    with pytest.raises(ShellyError, match="302"):
        await client.request(connection, "/redirect")
    with pytest.raises(ShellyError, match="256 KiB"):
        await client.request(connection, "/large")
