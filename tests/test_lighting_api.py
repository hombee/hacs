"""Exercise the public WebSocket contract through authenticated HA clients."""

import asyncio
import json
from itertools import count
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.light import ColorMode
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from test_managed_lighting import _light_action, _setup_managed, _setup_source_light

from custom_components.hombee.lighting_configuration import configuration_revision

REQUEST_IDS = count(1)


@pytest.fixture
async def lighting(hass):
    source = await _setup_source_light(hass, False, ColorMode.BRIGHTNESS)
    area = ar.async_get(hass).async_create("Kitchen")
    er.async_get(hass).async_update_entity("light.kitchen", area_id=area.id)
    hass.states.async_set(
        "sensor.kitchen_lux",
        "25",
        {"device_class": "illuminance", "unit_of_measurement": "lx"},
    )
    entry = await _setup_managed(hass)
    await entry.runtime_data.async_set_activity("default", "night")
    return entry, source, area.id


async def request(client, kind="get", **data):
    await client.send_json(
        {"id": next(REQUEST_IDS), "type": f"hombee/lighting/{kind}", **data}
    )
    return await client.receive_json()


async def edit(client, revision, operation, data):
    return await request(
        client, "update", expected_revision=revision, operation=operation, data=data
    )


async def test_read_contract_is_pure_and_paginated(hass, hass_ws_client, lighting):
    _, source, _ = lighting
    client = await hass_ws_client(hass)
    result = await request(client)
    assert result["success"]
    snapshot = result["result"]
    assert snapshot["api_version"] == 1
    assert snapshot["lights"][0]["supports_color_temperature"] is False
    assert snapshot["sensors"][0]["value"] == 25
    assert source.calls == []
    snapshot["sensors"][0]["last_reported"] = "2026-09-05T12:00:00+00:00"
    fixture = Path(__file__).parent / "fixtures" / "lighting_api_snapshot.json"
    assert snapshot == json.loads(fixture.read_text())
    page = (await request(client, limit=1, offset=1))["result"]
    assert page["lights"] == page["sensors"] == page["areas"] == []
    assert page["configuration"] == snapshot["configuration"]
    assert page["totals"] == {"areas": 1, "lights": 1, "sensors": 1}


async def test_all_edits_partial_updates_and_reload(hass, hass_ws_client, lighting):
    entry, source, area = lighting
    client = await hass_ws_client(hass)
    snapshot = (await request(client))["result"]
    operations = [
        (
            "profile",
            {
                "profile_key": area,
                "settings": {
                    "illuminance_sensor": "sensor.kitchen_lux",
                    "night_brightness": 11,
                },
            },
        ),
        ("profile", {"profile_key": area, "settings": {"task_lux": 450}}),
        ("light", {"entity_id": "light.kitchen", "settings": {"min_brightness": 7}}),
        ("activity", {"profile_key": area, "activity": "reading"}),
        ("enabled", {"brightness_enabled": False, "color_enabled": False}),
        ("resume", {"entity_ids": ["light.kitchen"]}),
    ]
    for operation, data in operations:
        response = await edit(client, snapshot["revision"], operation, data)
        assert response["success"], response
        snapshot = response["result"]
    profile = snapshot["configuration"]["profiles"][area]
    assert profile["night_brightness"] == 11
    assert profile["task_lux"] == 450
    assert profile["illuminance_sensor"] == "sensor.kitchen_lux"
    assert source.calls == []
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    restored = (await request(client))["result"]
    assert restored["revision"] == snapshot["revision"]
    assert restored["configuration"] == snapshot["configuration"]
    response = await edit(
        client, restored["revision"], "reset_profile", {"profile_key": area}
    )
    assert response["success"]
    assert area not in response["result"]["configuration"]["profiles"]
    assert area not in response["result"]["configuration"]["activities"]


@pytest.mark.parametrize(
    ("operation", "data"),
    [
        ("profile", {"profile_key": "missing", "settings": {"day_lux": 100}}),
        (
            "profile",
            {
                "profile_key": "default",
                "settings": {"illuminance_sensor": "sensor.kitchen_lux"},
            },
        ),
        (
            "profile",
            {
                "profile_key": "kitchen",
                "settings": {"illuminance_sensor": "sensor.missing"},
            },
        ),
        ("profile", {"profile_key": "default", "settings": {"wake_time": "25:00"}}),
        (
            "profile",
            {"profile_key": "default", "settings": {"day_brightness": "Infinity"}},
        ),
        ("profile", {"profile_key": "default", "settings": {"unknown_field": 1}}),
        ("profile", {"profile_key": "default", "settings": {}}),
        ("reset_profile", {"profile_key": "default"}),
        ("activity", {"profile_key": "default", "activity": "inherit"}),
        ("enabled", {}),
        (
            "light",
            {"entity_id": "light.kitchen_physical", "settings": {"adapt_color": False}},
        ),
        (
            "light",
            {
                "entity_id": "light.kitchen",
                "settings": {"min_brightness": 90, "max_brightness": 10},
            },
        ),
        ("resume", {"entity_ids": ["light.kitchen", "light.missing"]}),
        (
            "resume",
            {"entity_ids": ["light.kitchen"], "brightness": False, "color": False},
        ),
    ],
)
async def test_invalid_edits_have_no_effect(
    hass, hass_ws_client, lighting, operation, data
):
    entry, source, _ = lighting
    await _light_action(hass, brightness=77)
    before = configuration_revision(entry.runtime_data)
    client = await hass_ws_client(hass)
    response = await edit(client, before, operation, data)
    assert response["success"] is False
    assert response["error"]["code"] == "hombee_lighting_invalid"
    assert configuration_revision(entry.runtime_data) == before
    assert next(iter(entry.runtime_data.mappings.values())).brightness_override
    assert len(source.calls) == 1


async def test_stale_and_simultaneous_writes(hass, hass_ws_client, lighting):
    entry, _, _ = lighting
    client = await hass_ws_client(hass)
    revision = configuration_revision(entry.runtime_data)
    await entry.runtime_data.async_configure_profile(
        "default", {"night_brightness": 12}, patch=True
    )
    stale = await edit(
        client, revision, "activity", {"profile_key": "default", "activity": "reading"}
    )
    assert stale["error"]["code"] == "hombee_lighting_conflict"
    clients = [client, await hass_ws_client(hass)]
    revision = configuration_revision(entry.runtime_data)
    responses = await asyncio.gather(
        *[
            edit(
                item,
                revision,
                "activity",
                {"profile_key": "default", "activity": activity},
            )
            for item, activity in zip(clients, ("reading", "relax"), strict=True)
        ]
    )
    assert sorted(response["success"] for response in responses) == [False, True]


async def test_save_failure_rolls_back_configuration(hass, hass_ws_client, lighting):
    entry, source, _ = lighting
    client = await hass_ws_client(hass)
    before = (await request(client))["result"]
    with patch.object(
        entry.runtime_data._store, "async_save", AsyncMock(side_effect=OSError)
    ):
        response = await edit(
            client,
            before["revision"],
            "light",
            {
                "entity_id": "light.kitchen",
                "settings": {"min_brightness": 20},
            },
        )
    assert response["error"]["code"] == "hombee_lighting_storage_failed"
    assert (await request(client))["result"]["configuration"] == before["configuration"]
    assert source.calls == []


async def test_non_admin_cannot_read_or_write(
    hass, hass_ws_client, hass_read_only_access_token, lighting
):
    client = await hass_ws_client(hass, access_token=hass_read_only_access_token)
    for kind, data in [
        ("get", {}),
        (
            "update",
            {
                "expected_revision": "0" * 64,
                "operation": "enabled",
                "data": {"brightness_enabled": False},
            },
        ),
    ]:
        response = await request(client, kind, **data)
        assert response["error"]["code"] == "unauthorized"


async def test_unloaded_entry_is_unavailable(hass, hass_ws_client, lighting):
    entry, _, _ = lighting
    client = await hass_ws_client(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    response = await request(client)
    assert response["error"]["code"] == "hombee_lighting_not_loaded"
