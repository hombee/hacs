"""Exercise the Shelly bridge through authenticated HA WebSocket clients."""

import json
from itertools import count
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from zeroconf import ServiceStateChange

from custom_components.hombee.shelly_api import async_setup_shelly_api
from custom_components.hombee.shelly_client import ShellyError, ShellyTarget

IDENTITY = "shelly-aabbccddeeff"
INFO = {
    "id": "shellyplus1-aabbccddeeff",
    "mac": "AABBCCDDEEFF",
    "gen": 2,
    "model": "SNSW-001X16EU",
    "ver": "1.7.0",
    "auth_en": True,
}
REQUEST_IDS = count(1)


@pytest.fixture
async def bridge(hass):
    await async_setup_shelly_api(hass)
    bridge = hass.data["hombee"]["shelly_bridge"]
    bridge.targets[IDENTITY] = ShellyTarget("192.168.1.50", info=INFO)
    bridge.client.connection = AsyncMock(return_value="connection")
    bridge.client.identify = AsyncMock(return_value=INFO)
    bridge.client.methods = AsyncMock(
        return_value=[
            "Shelly.GetConfig",
            "Shelly.GetStatus",
            "Shelly.GetComponents",
            "Future.SetConfig",
        ]
    )
    bridge.client.rpc = AsyncMock(
        return_value={
            "result": {"future_field": [1, None, {"value": True}]},
            "rpc_error": None,
        }
    )
    return bridge


async def request(client, kind, **params):
    await client.send_json(
        {"id": next(REQUEST_IDS), "type": f"hombee/shelly/{kind}", **params}
    )
    return await client.receive_json()


async def test_list_is_paginated_and_does_not_call_devices(
    hass, hass_ws_client, bridge
):
    client = await hass_ws_client(hass)
    result = await request(client, "list", discover=False)
    assert result["success"]
    assert result["result"]["devices"][0]["device_id"] == IDENTITY
    assert result["result"]["documentation"]["api"].endswith("/gen2/")
    assert "password" not in str(result)
    page = await request(client, "list", discover=False, offset=1, limit=1)
    assert page["result"]["devices"] == []
    assert page["result"]["total"] == 1
    bridge.client.connection.assert_not_called()


async def test_inspection_reads_only_advertised_methods_and_registers_host(
    hass, hass_ws_client, bridge
):
    client = await hass_ws_client(hass)
    bridge.targets.clear()
    result = await request(client, "inspect", host="192.168.1.50")
    assert result["success"]
    assert result["result"]["device"]["device_id"] == IDENTITY
    fixture = Path(__file__).parent / "fixtures" / "shelly_inspection_snapshot.json"
    assert result["result"] == json.loads(fixture.read_text())
    assert IDENTITY in bridge.targets
    assert result["result"]["methods"][-1] == "Future.SetConfig"
    assert set(result["result"]["reads"]) == {
        "Shelly.GetConfig",
        "Shelly.GetStatus",
        "Shelly.GetComponents",
    }
    assert bridge.client.rpc.call_count == 3
    bridge.client.rpc.assert_any_await(
        "connection", "Shelly.GetComponents", {"include": ["config", "status"]}
    )


async def test_future_method_and_nested_params_pass_unchanged(
    hass, hass_ws_client, bridge
):
    client = await hass_ws_client(hass)
    params = {"id": 0, "config": {"unknown": [None, True, 12.5, {"value": "new"}]}}
    result = await request(
        client, "call", device_id=IDENTITY, method="Future.SetConfig", params=params
    )
    assert result["success"]
    assert result["result"]["rpc_error"] is None
    bridge.client.identify.assert_awaited_once_with("connection", IDENTITY)
    bridge.client.rpc.assert_awaited_once_with("connection", "Future.SetConfig", params)


async def test_vendor_errors_are_preserved(hass, hass_ws_client, bridge):
    bridge.client.rpc.return_value = {
        "result": None,
        "rpc_error": {"code": -103, "message": "Invalid argument", "new_detail": [2]},
    }
    client = await hass_ws_client(hass)
    result = await request(
        client, "call", device_id=IDENTITY, method="Future.SetConfig"
    )
    assert result["result"]["rpc_error"]["code"] == -103
    assert result["result"]["rpc_error"]["new_detail"] == [2]


@pytest.mark.parametrize(
    "kind,params",
    [
        ("list", {"discover": False}),
        ("inspect", {"device_id": IDENTITY}),
        ("call", {"device_id": IDENTITY, "method": "Future.SetConfig"}),
    ],
)
async def test_all_commands_require_admin(
    hass, hass_ws_client, hass_read_only_access_token, bridge, *, kind, params
):
    client = await hass_ws_client(hass, access_token=hass_read_only_access_token)
    result = await request(client, kind, **params)
    assert result["success"] is False
    assert result["error"]["code"] == "unauthorized"
    bridge.client.connection.assert_not_called()


async def test_no_rpc_after_identity_failure_or_unknown_method(
    hass, hass_ws_client, bridge
):
    client = await hass_ws_client(hass)
    result = await request(client, "call", device_id=IDENTITY, method="Unknown.Set")
    assert result["error"]["code"] == "shelly_method_unavailable"
    bridge.client.rpc.assert_not_called()
    bridge.client.identify.side_effect = ShellyError(
        "shelly_identity_changed", "Different MAC"
    )
    result = await request(
        client, "call", device_id=IDENTITY, method="Future.SetConfig"
    )
    assert result["error"]["code"] == "shelly_identity_changed"
    bridge.client.rpc.assert_not_called()


async def test_uncertain_rpc_is_not_retried(hass, hass_ws_client, bridge):
    bridge.client.rpc.side_effect = ShellyError("shelly_unavailable", "Timeout")
    client = await hass_ws_client(hass)
    result = await request(
        client, "call", device_id=IDENTITY, method="Future.SetConfig"
    )
    assert result["error"]["code"] == "shelly_call_unconfirmed"
    assert "Do not retry" in result["error"]["message"]
    bridge.client.rpc.assert_awaited_once()


async def test_credentials_and_host_follow_current_ha_entry(hass, bridge):
    entry = MockConfigEntry(
        domain="shelly",
        title="Kitchen",
        unique_id="aabbccddeeff",
        data={"host": "192.168.1.50", "gen": 2, "password": "secret"},
    )
    entry.add_to_hass(hass)
    target = bridge.target(IDENTITY)
    assert target.entry_id == entry.entry_id
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, "host": "192.168.1.51"}
    )
    assert bridge.target(IDENTITY).host == "192.168.1.51"
    assert "secret" not in str(bridge.descriptor(IDENTITY, target))
    await bridge.inspect({"host": "192.168.1.51", "port": 80})
    assert bridge.client.connection.call_args.args[0].entry_id == entry.entry_id


async def test_manual_inspection_updates_an_existing_device_address(bridge):
    original = bridge.targets[IDENTITY]
    result = await bridge.inspect({"host": "192.168.1.51", "port": 80})
    assert result["device"]["host"] == "192.168.1.51"
    assert bridge.target(IDENTITY) is original
    assert original.host == "192.168.1.51"
    await bridge.call(IDENTITY, "Future.SetConfig", {})
    assert bridge.client.connection.call_args.args[0] is original


@pytest.mark.parametrize(
    "params", [{}, {"device_id": IDENTITY, "host": "192.168.1.50"}]
)
async def test_inspection_requires_exactly_one_target(
    hass, hass_ws_client, bridge, params
):
    client = await hass_ws_client(hass)
    result = await request(client, "inspect", **params)
    assert result["error"]["code"] == "shelly_invalid_target"
    bridge.client.connection.assert_not_called()


async def test_mdns_discovery_filters_and_verifies_devices(hass, bridge):
    bridge.browser = object()
    bridge.service_changed(
        None, "_http._tcp.local.", "printer._http._tcp.local.", ServiceStateChange.Added
    )
    assert bridge.services == {}
    name = "shellyplus1._http._tcp.local."
    bridge.service_changed(None, "_http._tcp.local.", name, ServiceStateChange.Added)
    info = SimpleNamespace(port=80, parsed_addresses=lambda: ["192.168.1.50"])
    zc = SimpleNamespace(async_get_service_info=AsyncMock(return_value=info))
    bridge.targets.clear()
    with patch(
        "custom_components.hombee.shelly_api.zeroconf.async_get_async_instance",
        AsyncMock(return_value=zc),
    ):
        assert await bridge.discover() == []
    assert bridge.targets[IDENTITY].host == "192.168.1.50"
    bridge.client.rpc.assert_not_called()
    bridge.service_changed(None, "_http._tcp.local.", name, ServiceStateChange.Removed)
    assert bridge.services == {}


async def test_browser_is_lazy_and_cancelled_on_shutdown(hass, bridge):
    assert bridge.browser is None
    zc = SimpleNamespace(zeroconf=object())
    browser = MagicMock(async_cancel=AsyncMock())
    with (
        patch(
            "custom_components.hombee.shelly_api.zeroconf.async_get_async_instance",
            AsyncMock(return_value=zc),
        ),
        patch(
            "custom_components.hombee.shelly_api.AsyncServiceBrowser",
            return_value=browser,
        ) as create,
    ):
        await bridge.start()
    create.assert_called_once_with(
        zc.zeroconf,
        ["_shelly._tcp.local.", "_http._tcp.local."],
        handlers=[bridge.service_changed],
    )
    await hass.async_stop()
    browser.async_cancel.assert_awaited_once()
