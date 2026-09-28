"""Doorbell rings, persisted delivery and entrance permissions through HA."""

import time
from datetime import UTC, datetime
from itertools import count
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component

from custom_components.hombee.doorbell_api import async_setup_doorbells
from custom_components.hombee.doorbell_runtime import DoorbellRuntime

IDS = count(1)


@pytest.fixture
async def doorbells(hass):
    await async_setup_component(hass, "websocket_api", {})
    for entity, state in (
        ("event.front", "2026-01-01T00:00:00+00:00"),
        ("binary_sensor.gate", "off"),
        ("camera.front", "idle"),
        ("camera.driveway", "idle"),
        ("lock.front", "locked"),
    ):
        hass.states.async_set(entity, state)
    await async_setup_doorbells(hass)
    runtime = hass.data["hombee"]["doorbells"]
    definitions = [
        {
            "id": "front",
            "name": "Front door",
            "enabled": True,
            "source": {"kind": "event", "entityId": "event.front", "eventType": "ring"},
            "cameras": ["camera.front", "camera.driveway"],
            "action": {"kind": "unlock", "entityId": "lock.front", "label": "Unlock"},
        },
        {
            "id": "gate",
            "name": "Gate",
            "enabled": True,
            "source": {
                "kind": "state",
                "entityId": "binary_sensor.gate",
                "fromState": "off",
                "toState": "on",
            },
            "cameras": ["camera.driveway"],
            "action": None,
        },
    ]
    await runtime.save(0, str(uuid4()), definitions)
    return runtime


async def request(client, kind, **data):
    await client.send_json(
        {"id": next(IDS), "type": f"hombee/doorbells/{kind}", **data}
    )
    return await client.receive_json()


async def test_ring_sources_and_camera_associations(hass, doorbells):
    with patch.object(doorbells.delivery, "enqueue", new_callable=AsyncMock) as enqueue:
        hass.states.async_set(
            "event.front", datetime.now(UTC).isoformat(), {"event_type": "ring"}
        )
        hass.states.async_set("binary_sensor.gate", "on")
        await hass.async_block_till_done()
        calls = {
            call.args[0]["doorbellId"]: call.args for call in enqueue.call_args_list
        }
        assert set(calls) == {"front", "gate"}
        assert calls["front"][1] == "camera.front"
        assert calls["gate"][1] == "camera.driveway"
        assert calls["front"][0]["eventId"] != calls["gate"][0]["eventId"]
        enqueue.reset_mock()
        hass.states.async_set(
            "event.front", "2020-01-01T00:00:00+00:00", {"event_type": "ring"}
        )
        hass.states.async_set("binary_sensor.gate", "unavailable")
        hass.states.async_set("binary_sensor.gate", "on")
        await hass.async_block_till_done()
        enqueue.assert_not_called()


async def test_api_permissions_revision_and_action_replay(
    hass, hass_ws_client, hass_read_only_access_token, doorbells
):
    admin = await hass_ws_client(hass)
    reader = await hass_ws_client(hass, access_token=hass_read_only_access_token)
    snapshot = (await request(reader, "status"))["result"]
    assert not snapshot["canManage"]
    assert snapshot["doorbells"][0]["action"] is None
    assert snapshot["doorbells"][0]["cameras"] == ["camera.front", "camera.driveway"]
    rejected = await request(
        reader,
        "save",
        instanceId=doorbells.instance_id,
        expectedRevision=1,
        requestId=str(uuid4()),
        doorbells=[],
    )
    assert rejected["error"]["code"] == "unauthorized"
    calls = []

    async def unlock(call):
        calls.append(call)

    hass.services.async_register("lock", "unlock", unlock)
    command = {
        "instanceId": doorbells.instance_id,
        "doorbellId": "front",
        "configurationRevision": 1,
        "commandId": str(uuid4()),
        "expiresAt": int(time.time() * 1000) + 30_000,
    }
    assert not (await request(reader, "open", **command))["success"]
    assert (await request(admin, "open", **command))["result"]["status"] == "sent"
    restored = DoorbellRuntime(hass)
    await restored.load()
    hass.data["hombee"]["doorbells"] = restored
    assert (await request(admin, "open", **command))["result"][
        "status"
    ] == "already_sent"
    assert len(calls) == 1
    assert calls[0].data["entity_id"] == "lock.front"
    assert calls[0].context.user_id is not None
    command["commandId"] = str(uuid4())
    command["expiresAt"] = int(time.time() * 1000) - 1
    assert not (await request(admin, "open", **command))["success"]
    command["expiresAt"] += 30_000
    command["configurationRevision"] = 0
    assert not (await request(admin, "open", **command))["success"]
    assert len(calls) == 1

    command["configurationRevision"] = 1
    command["commandId"] = str(uuid4())
    failed_calls = []

    async def failing_unlock(call):
        failed_calls.append(call)
        raise HomeAssistantError("Connection lost")

    hass.services.async_register("lock", "unlock", failing_unlock)
    assert not (await request(admin, "open", **command))["success"]
    recovered = DoorbellRuntime(hass)
    await recovered.load()
    hass.data["hombee"]["doorbells"] = recovered
    assert not (await request(admin, "open", **command))["success"]
    assert len(failed_calls) == 1


async def test_registry_rename_and_replacement_fail_closed(hass, doorbells):
    registry = er.async_get(hass)
    hass.states.async_remove("lock.front")
    registry.async_get_or_create(
        "lock", "test", "stable-lock", suggested_object_id="front"
    )
    hass.states.async_set("lock.front", "locked")
    await doorbells.save(1, str(uuid4()), doorbells.data["doorbells"])
    registry.async_update_entity("lock.front", new_entity_id="lock.renamed")
    assert doorbells.doorbells()[0]["action"]["entityId"] == "lock.renamed"
    registry.async_remove("lock.renamed")
    registry.async_get_or_create(
        "lock", "test", "replacement", suggested_object_id="front"
    )
    assert doorbells.doorbells()[0]["action"] is None
    before = doorbells.data
    with (
        patch.object(doorbells.store, "async_save", AsyncMock(side_effect=OSError)),
        pytest.raises(OSError),
    ):
        await doorbells.save(2, str(uuid4()), [])
    assert doorbells.data == before


async def test_camera_failure_retries_after_restart(
    hass, doorbells, aioclient_mock, freezer
):
    await doorbells.configure({"gatewayUrl": "https://hombee.app", "token": "x" * 72})
    url = f"https://hombee.app/api/doorbells/instances/{doorbells.instance_id}/events"
    aioclient_mock.post(url, status=503)
    with patch(
        "custom_components.hombee.doorbell_delivery.async_get_image",
        AsyncMock(side_effect=HomeAssistantError),
    ):
        await doorbells.ring("front", "physical-event")
    assert len(doorbells.delivery.jobs) == 1
    event = doorbells.delivery.jobs[0]["event"]
    assert event["image"] is None
    doorbells.delivery.stop()
    restored = DoorbellRuntime(hass)
    await restored.load()
    aioclient_mock.clear_requests()
    aioclient_mock.post(url, status=202)
    freezer.tick(5)
    await restored.delivery.drain()
    assert restored.delivery.jobs == []
    assert aioclient_mock.mock_calls[-1][2]["eventId"] == event["eventId"]
