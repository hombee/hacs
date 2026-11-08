"""Protocol, official registries, HA freshness, delivery and local safety tests."""

import asyncio
import json
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import UTC, datetime
from itertools import count
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import voluptuous as vol
from homeassistant.core import Context
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.loader import async_get_integration
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.hombee.energy.adapter import observation
from custom_components.hombee.energy.api import async_setup_energy
from custom_components.hombee.energy.discovery import discover
from custom_components.hombee.energy.models import BINDING, PUBLISHER
from custom_components.hombee.energy.runtime import EnergyRuntime

IDS = count(1)


@pytest.fixture
async def energy(hass, aioclient_mock):
    await async_setup_component(hass, "websocket_api", {})
    await async_get_integration(hass, "solaredge_modbus")
    entry = MockConfigEntry(
        domain="solaredge_modbus", unique_id="INV-123", title="Garage inverter"
    )
    entry.add_to_hass(hass)
    devices, entities = dr.async_get(hass), er.async_get(hass)
    for identity, keys in (
        ("INV-123", (("ac_power", "2000", "W"), ("on_grid", "on", None))),
        ("INV-123_meter_M1", (("ac_power", "500", "W"),)),
        (
            "INV-123_battery_B1",
            (("dc_power", "-600", "W"), ("state_of_energy", "55", "%")),
        ),
    ):
        device = devices.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={("solaredge_modbus", identity)},
            name=identity,
            serial_number=identity,
        )
        for key, state, unit in keys:
            domain = "binary_sensor" if key == "on_grid" else "sensor"
            entity = entities.async_get_or_create(
                domain,
                "solaredge_modbus",
                f"{identity}_{key}",
                config_entry=entry,
                device_id=device.id,
            )
            hass.states.async_set(
                entity.entity_id, state, {"unit_of_measurement": unit} if unit else {}
            )
    load = entities.async_get_or_create(
        "switch", "test", "load", suggested_object_id="load"
    )
    hass.states.async_set(load.entity_id, "off")
    await async_setup_energy(hass)
    runtime = hass.data["hombee"]["energy"]
    source = discover(hass)[0]
    binding = BINDING(
        {
            "bindingId": "binding-1",
            "site": {"provider": "solaredge", "id": "site-1"},
            "sourceIds": [entry.entry_id],
            "gridMeterId": source.meters[0].id,
            "batteryIds": [source.batteries[0].id],
            "policy": {},
        }
    )
    runtime.test_binding = binding
    runtime.test_entry = entry
    return runtime


def message(runtime, publisher_id="account-a", revision=0, **kwargs):
    return {
        "protocol": 1,
        "instanceId": runtime.instance_id,
        "publisherId": publisher_id,
        "operationId": f"request-{next(IDS)}",
        "expectedRevision": revision,
        "bindings": [deepcopy(runtime.test_binding)],
        **kwargs,
    }


async def configure(runtime, publisher_id="account-a", **kwargs):
    msg = message(
        runtime,
        publisher_id,
        publisher={
            "url": "https://hombee.app",
            "token": "e" * 72,
            "generation": f"generation-{publisher_id}",
            "bindingRevision": 1,
        },
        **kwargs,
    )
    await runtime.update(msg, "configure")
    return runtime.publishers[publisher_id]


async def request(client, kind, **kwargs):
    await client.send_json({"id": next(IDS), "type": f"hombee/energy/{kind}", **kwargs})
    return await client.receive_json()


async def test_admin_protocol_identity_redaction_and_gateway(
    hass, energy, hass_ws_client, hass_read_only_access_token
):
    reader = await hass_ws_client(hass, access_token=hass_read_only_access_token)
    assert (await request(reader, "status"))["error"]["code"] == "unauthorized"
    admin = await hass_ws_client(hass)
    snapshot = (await request(admin, "status"))["result"]
    assert snapshot["protocol"] == 1 and snapshot["instanceId"] == energy.instance_id
    assert snapshot["revision"] == 0 and snapshot["bindings"] == []
    msg = message(
        energy,
        publisher={
            "url": "http://evil.example",
            "token": "e" * 72,
            "generation": "g",
            "bindingRevision": 1,
        },
    )
    assert not (await request(admin, "configure", **msg))["success"]
    msg["publisher"]["url"] = "https://hombee.app"
    msg["instanceId"] = "wrong-instance"
    assert not (await request(admin, "configure", **msg))["success"]
    await configure(energy)
    scoped = (await request(admin, "status", publisherId="account-a"))["result"]
    assert scoped["configured"] and scoped["publisherId"] == "account-a"
    assert "token" not in str(scoped) and "e" * 72 not in str(scoped)
    assert (await request(admin, "status"))["result"]["bindings"] == []


async def test_registry_only_official_and_rename(hass, energy):
    entities = er.async_get(hass)
    official = discover(hass)[0]
    entity_id = official.inverter.points["ac_power"]
    entities.async_update_entity(entity_id, new_entity_id="sensor.renamed_inverter")
    assert discover(hass)[0].inverter.points["ac_power"] == "sensor.renamed_inverter"
    fake = MockConfigEntry(domain="solaredge_modbus_multi", unique_id="fake")
    fake.add_to_hass(hass)
    entities.async_get_or_create(
        "sensor", "solaredge_modbus_multi", "INV-123_ac_power", config_entry=fake
    )
    hass.states.async_set(
        "sensor.solaredge_named_like_official", "9000", {"unit_of_measurement": "W"}
    )
    assert len(discover(hass)) == 1
    assert official.id == energy.test_entry.entry_id
    assert official.batteries[0].id != official.meters[0].id
    entities.async_update_entity(
        "sensor.renamed_inverter", disabled_by=er.RegistryEntryDisabler.USER
    )
    assert "ac_power" not in discover(hass)[0].inverter.points


async def test_historical_custom_package_same_domain_is_rejected(hass, energy):
    integration = await async_get_integration(hass, "solaredge_modbus")
    assert integration.is_built_in and discover(hass)
    with patch.object(integration, "pkg_path", "custom_components.solaredge_modbus"):
        assert not integration.is_built_in
        assert discover(hass) == []
        assert (
            observation(hass, energy.test_binding, discover(hass))["quality"]
            == "unavailable"
        )
        with pytest.raises(vol.Invalid, match="official"):
            await configure(energy)
    assert discover(hass)


async def test_units_signs_grid_charging_topology_and_unavailable(hass, energy):
    sources = discover(hass)
    value = observation(hass, energy.test_binding, sources)
    assert value["quality"] == "fresh" and value["gridState"] == "connected"
    assert value["telemetry"]["gridPower"] == -500
    assert value["telemetry"]["loadPower"] == 1500
    assert value["telemetry"]["batteryPower"] == 600
    assert value["telemetry"]["batterySoc"] == 55
    assert "solarPower" not in value["telemetry"]
    hass.states.async_set(
        sources[0].inverter.points["ac_power"], "-1", {"unit_of_measurement": "kW"}
    )
    hass.states.async_set(
        sources[0].meters[0].points["ac_power"], "-2500", {"unit_of_measurement": "W"}
    )
    value = observation(hass, energy.test_binding, sources)
    assert value["telemetry"]["gridPower"] == 2500
    assert value["telemetry"]["loadPower"] == 1500
    hass.states.async_set(sources[0].inverter.points["on_grid"], "unavailable")
    hass.states.async_set(sources[0].batteries[0].points["state_of_energy"], "unknown")
    value = observation(hass, energy.test_binding, sources)
    assert value["gridState"] == "unknown" and value["quality"] == "unavailable"
    assert "batterySoc" not in value["telemetry"]


async def test_reported_not_changed_and_coherent_group(hass, energy, freezer):
    sources = discover(hass)
    entity_id = sources[0].inverter.points["ac_power"]
    original_changed = hass.states.get(entity_id).last_changed
    freezer.tick(31)
    assert observation(hass, energy.test_binding, sources)["quality"] == "stale"
    for source in sources:
        for component in [source.inverter, *source.meters, *source.batteries]:
            for entity in component.points.values():
                state = hass.states.get(entity)
                hass.states.async_set(entity, state.state, state.attributes)
    assert hass.states.get(entity_id).last_changed == original_changed
    assert observation(hass, energy.test_binding, sources)["quality"] == "fresh"
    freezer.tick(6)
    hass.states.async_set(entity_id, "2001", {"unit_of_measurement": "W"})
    value = observation(hass, energy.test_binding, sources)
    assert value["quality"] == "stale"
    assert set(value["telemetry"]) == {"site", "timestamp"}


async def test_idempotency_revision_scope_and_restart(hass, energy):
    a = await configure(energy)
    msg = message(energy, revision=1)
    await energy.update(msg, "save")
    await energy.update(msg, "save")
    assert a.data["revision"] == 2
    msg["bindings"] = []
    with pytest.raises(vol.Invalid, match="different request"):
        await energy.update(msg, "save")
    with pytest.raises(vol.Invalid, match="Refresh"):
        await energy.update(message(energy), "save")
    b = await configure(energy, "account-b")
    await energy.update(
        message(energy, revision=2, bindingId="binding-1"), "unconfigure"
    )
    assert not a.data.get("publisher") and b.data["publisher"]["token"] == "e" * 72
    restored = EnergyRuntime(hass)
    await restored.load()
    assert restored.publishers["account-b"].data["bindings"] == b.data["bindings"]
    assert restored.publishers["account-a"].data["revision"] == 3


async def test_delivery_coalesce_retry_after_restart_and_revoke(
    hass, energy, aioclient_mock, freezer
):
    publisher = await configure(energy)
    url = f"https://hombee.app/api/energy/local/instances/{energy.instance_id}/observations"
    aioclient_mock.post(url, status=429, headers={"Retry-After": "60"})
    await publisher.sample()
    first = deepcopy(publisher.delivery.data["jobs"]["binding-1"])
    for _ in range(3):
        await publisher.sample()
    assert len(publisher.delivery.data["jobs"]) == 1
    assert publisher.delivery.data["jobs"]["binding-1"]["sequence"] > first["sequence"]
    assert aioclient_mock.call_count == 1
    restored = EnergyRuntime(hass)
    await restored.load()
    lane = restored.publishers["account-a"].delivery
    assert lane.data["jobs"]["binding-1"]["observedAtMs"] == first["observedAtMs"]
    freezer.tick(61)
    await lane.drain()
    assert lane.data["jobs"] == {}  # obsolete live values never replay
    aioclient_mock.clear_requests()
    aioclient_mock.post(url, status=403)
    # Refresh all sensors with an ordinary official report.
    for state in hass.states.async_all():
        hass.states.async_set(state.entity_id, state.state, state.attributes)
    await restored.publishers["account-a"].sample()
    assert lane.data["revoked"] and lane.data["jobs"] == {}
    await restored.publishers["account-a"].sample()
    assert aioclient_mock.call_count == 1


def load_policy():
    return {
        "enabled": True,
        "rules": [
            {
                "id": "export-load",
                "enabled": True,
                "conditions": [
                    {"field": "gridPower", "maximum": -100, "hysteresis": 50}
                ],
                "dwellSeconds": 10,
                "action": {"entityId": "switch.load", "service": "turnOn"},
            }
        ],
    }


async def test_local_dwell_readback_manual_override_offline(hass, energy, freezer):
    binding = deepcopy(energy.test_binding)
    binding["policy"] = load_policy()
    publisher = await configure(energy, bindings=[binding])

    async def turn_on(call):
        hass.states.async_set("switch.load", "on", context=call.context)

    hass.services.async_register("switch", "turn_on", turn_on)
    with patch.object(publisher.delivery, "drain", AsyncMock()):
        await publisher.sample()
        assert hass.states.get("switch.load").state == "off"
        freezer.tick(10)
        await publisher.sample()
        await hass.async_block_till_done()
        assert hass.states.get("switch.load").state == "on"
        hass.states.async_set("switch.load", "off", context=Context(user_id="person"))
        await hass.async_block_till_done()
        assert publisher.data["overrides"]["binding-1:export-load"]
        await publisher.sample()
        assert hass.states.get("switch.load").state == "off"
    restored = EnergyRuntime(hass)
    await restored.load()
    assert restored.publishers["account-a"].data["overrides"]["binding-1:export-load"]


async def test_policy_defaults_overlap_invalid_target_and_stale(hass, energy, freezer):
    publisher = await configure(energy)
    assert not publisher.data["bindings"][0]["policy"]["enabled"]
    binding = deepcopy(energy.test_binding)
    binding["policy"] = load_policy()
    await energy.update(message(energy, revision=1, bindings=[binding]), "save")
    with pytest.raises(vol.Invalid, match="only one"):
        await configure(energy, "account-b", bindings=[binding])
    invalid = deepcopy(binding)
    invalid["policy"]["rules"][0]["action"]["entityId"] = "number.inverter_limit"
    with pytest.raises(vol.Invalid):
        await energy.update(message(energy, revision=2, bindings=[invalid]), "save")
    inverter_switch = er.async_get(hass).async_get_or_create(
        "switch", "solaredge_modbus_multi", "inverter-write"
    )
    invalid["policy"]["rules"][0]["action"]["entityId"] = inverter_switch.entity_id
    with pytest.raises(vol.Invalid, match="load switch"):
        await energy.update(message(energy, revision=2, bindings=[invalid]), "save")
    freezer.tick(31)
    with patch.object(type(hass.services), "async_call", AsyncMock()) as call:
        await publisher.sample()
        call.assert_not_called()


async def test_heartbeat_with_phone_closed(hass, energy, freezer):
    publisher = await configure(energy)
    with patch.object(publisher, "sample", AsyncMock()) as sample:
        freezer.tick(10)
        async_fire_time_changed(hass, datetime.now(UTC))
        await hass.async_block_till_done()
        sample.assert_awaited_once()


async def test_shared_configure_and_observation_wire_fixtures(
    hass, energy, hass_ws_client
):
    """Mirror the Dart/Zod fixtures without renaming any protocol keys."""
    fixture = json.loads(
        (Path(__file__).parent / "fixtures/energy_configure.json").read_text()
    )
    assert PUBLISHER(fixture["publisher"]) == fixture["publisher"]
    assert BINDING(fixture["bindings"][0]) == fixture["bindings"][0]
    client = await hass_ws_client(hass)
    # Fixture identifiers describe another HA instance, so target matching rejects it.
    result = await request(client, "configure", **fixture)
    assert result["error"]["code"] == "invalid_energy_configuration"
    fixture["instanceId"] = energy.instance_id
    fixture["expectedRevision"] = 0
    fixture["publisher"]["bindingRevision"] = 1
    fixture["bindings"] = [energy.test_binding]
    result = await request(client, "configure", **fixture)
    assert result["result"]["publisherId"] == fixture["publisherId"]
    publisher = energy.publishers[fixture["publisherId"]]
    await publisher.delivery.enqueue(
        [observation(hass, energy.test_binding, discover(hass))]
    )
    actual = publisher.delivery.data["jobs"]["binding-1"]
    expected = json.loads(
        (Path(__file__).parent / "fixtures/energy_observation.json").read_text()
    )
    assert set(actual) == set(expected)
    assert set(actual["telemetry"]) == set(expected["telemetry"]) - {"solarPower"}
    assert "receivedAtMs" not in actual


async def test_model713_soc_only_and_zero_is_a_value(hass, energy):
    source = discover(hass)[0]
    registry = er.async_get(hass)
    for entity in list(registry.entities.values()):
        if entity.device_id == source.batteries[0].id:
            registry.async_remove(entity.entity_id)
    dr.async_get(hass).async_remove_device(source.batteries[0].id)
    entity = registry.async_get_or_create(
        "sensor",
        "solaredge_modbus",
        "INV-123_storage_state_of_charge",
        config_entry=energy.test_entry,
        device_id=source.inverter.id,
    )
    hass.states.async_set(entity.entity_id, "0", {"unit_of_measurement": "%"})
    source = discover(hass)[0]
    assert source.batteries[0].choice("battery")["availableFields"] == ["batterySoc"]
    binding = {**energy.test_binding, "batteryIds": [source.batteries[0].id]}
    value = observation(hass, binding, [source])
    assert value["telemetry"]["batterySoc"] == 0
    assert "batteryPower" not in value["telemetry"]


async def test_readback_uncertain_stops_retries_and_target_rename(
    hass, energy, aioclient_mock
):
    aioclient_mock.post(
        f"https://hombee.app/api/energy/local/instances/{energy.instance_id}/observations",
        status=202,
    )
    binding = deepcopy(energy.test_binding)
    binding["policy"] = load_policy()
    binding["policy"]["rules"][0]["dwellSeconds"] = 0
    publisher = await configure(energy, bindings=[binding])
    registry = er.async_get(hass)
    registry.async_update_entity("switch.load", new_entity_id="switch.water_heater")
    hass.states.async_remove("switch.load")
    hass.states.async_set("switch.water_heater", "off")
    call = AsyncMock()
    hass.services.async_register("switch", "turn_on", call)
    await publisher.sample()
    assert call.await_count == 1
    assert publisher.data["overrides"]["binding-1:export-load"]
    await publisher.sample()
    assert call.await_count == 1
    registry.async_remove("switch.water_heater")
    assert publisher.resolve_action("binding-1:export-load") is None


async def test_each_rule_rechecks_input_age_after_previous_action(
    hass, energy, freezer
):
    binding = deepcopy(energy.test_binding)
    binding["policy"] = load_policy()
    rule = binding["policy"]["rules"][0]
    rule["dwellSeconds"] = 0
    other = deepcopy(rule)
    other["id"] = "second"
    other["action"]["entityId"] = "switch.second"
    binding["policy"]["rules"].append(other)
    er.async_get(hass).async_get_or_create(
        "switch", "test", "second", suggested_object_id="second"
    )
    hass.states.async_set("switch.second", "off")
    publisher = await configure(energy, bindings=[binding])
    calls = []

    async def turn_on(call):
        calls.append(call.data["entity_id"])
        hass.states.async_set("switch.load", "on", context=call.context)
        freezer.tick(31)

    hass.services.async_register("switch", "turn_on", turn_on)
    await publisher.sample()
    assert len(calls) == 1
    assert hass.states.get("switch.second").state == "off"
    assert publisher.policy.diagnostics["binding-1:second"] == "inputs_not_fresh"


async def test_revocation_and_offline_authorization_lease(
    hass, energy, freezer, aioclient_mock
):
    aioclient_mock.post(
        f"https://hombee.app/api/energy/local/instances/{energy.instance_id}/observations",
        status=503,
    )
    binding = deepcopy(energy.test_binding)
    binding["policy"] = load_policy()
    binding["policy"]["rules"][0]["dwellSeconds"] = 0
    publisher = await configure(energy, bindings=[binding])
    assert publisher.authorization_reason("binding-1") is None
    freezer.tick(24 * 60 * 60 + 1)
    for state in hass.states.async_all():
        hass.states.async_set(state.entity_id, state.state, state.attributes)
    await publisher.sample()
    assert (
        publisher.policy.diagnostics["binding-1:export-load"]
        == "policy_authorization_expired"
    )
    assert (
        "binding-1:policy_authorization_expired" in publisher.snapshot()["diagnostics"]
    )
    publisher.delivery.data["revoked"] = True
    await energy.update(message(energy, revision=1, bindings=[binding]), "save")
    assert publisher.delivery.data["revoked"]  # an ordinary save cannot repair auth
    await publisher.sample()
    assert (
        publisher.policy.diagnostics["binding-1:export-load"]
        == "publisher_authorization_required"
    )
    assert hass.states.get("switch.load").state == "off"


async def test_unchanged_manual_command_pauses_rule(hass, energy):
    binding = deepcopy(energy.test_binding)
    binding["policy"] = load_policy()
    publisher = await configure(energy, bindings=[binding])
    hass.services.async_register("switch", "turn_off", AsyncMock())
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.load"}, blocking=True
    )
    await hass.async_block_till_done()
    assert publisher.data["overrides"]["binding-1:export-load"]


async def test_inverter_group_and_partial_unconfigure(hass, energy):
    entry = MockConfigEntry(
        domain="solaredge_modbus", unique_id="INV-SECOND", title="Second inverter"
    )
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("solaredge_modbus", "INV-SECOND")},
        serial_number="INV-SECOND",
    )
    entity = er.async_get(hass).async_get_or_create(
        "sensor",
        "solaredge_modbus",
        "INV-SECOND_ac_power",
        config_entry=entry,
        device_id=device.id,
    )
    hass.states.async_set(entity.entity_id, "1000", {"unit_of_measurement": "W"})
    grouped = deepcopy(energy.test_binding)
    grouped["sourceIds"].append(entry.entry_id)
    value = observation(hass, grouped, discover(hass))
    assert value["telemetry"]["gridPower"] == -500  # selected PCC meter counted once
    assert value["telemetry"]["loadPower"] == 2500
    assert value["sourceId"] == grouped["sourceIds"][0]
    assert value["gridState"] == "unknown"  # second inverter has no grid-state evidence
    second = BINDING(
        {
            "bindingId": "second-binding",
            "site": {"provider": "solaredge", "id": "second-site"},
            "sourceIds": [entry.entry_id],
        }
    )
    publisher = await configure(energy, bindings=[energy.test_binding, second])
    await energy.update(
        message(energy, revision=1, bindingId="binding-1"), "unconfigure"
    )
    assert publisher.data["bindings"] == [second]
    assert publisher.data["publisher"]["bindingRevision"] == 2
    assert publisher.data["publisher"]["generation"] == "generation-account-a"


async def test_successful_delivery_renews_auth_and_persistent_sequence(
    hass, energy, aioclient_mock, freezer
):
    publisher = await configure(energy)
    url = f"https://hombee.app/api/energy/local/instances/{energy.instance_id}/observations"
    aioclient_mock.post(url, status=202)
    await publisher.sample()
    first_sequence = publisher.delivery.data["sequence"]
    first_authority = publisher.delivery.data["bindings"]["binding-1"][
        "authorizedUntil"
    ]
    restored = EnergyRuntime(hass)
    await restored.load()
    rebooted = restored.publishers["account-a"]
    assert rebooted.session_id != publisher.session_id
    freezer.tick(10)
    await rebooted.sample()
    assert rebooted.delivery.data["sequence"] > first_sequence
    assert (
        rebooted.delivery.data["bindings"]["binding-1"]["authorizedUntil"]
        > first_authority
    )
    assert aioclient_mock.mock_calls[-1][2]["sessionId"] == rebooted.session_id


async def test_manual_command_during_previous_rule_cannot_be_overridden(hass, energy):
    binding = deepcopy(energy.test_binding)
    binding["policy"] = load_policy()
    rule = binding["policy"]["rules"][0]
    rule["dwellSeconds"] = 0
    other = deepcopy(rule)
    other["id"] = "second"
    other["action"]["entityId"] = "switch.second"
    binding["policy"]["rules"].append(other)
    er.async_get(hass).async_get_or_create(
        "switch", "test", "second", suggested_object_id="second"
    )
    hass.states.async_set("switch.second", "off")
    publisher = await configure(energy, bindings=[binding])
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def turn_on(call):
        calls.append(call.data["entity_id"])
        entered.set()
        await release.wait()
        hass.states.async_set("switch.load", "on", context=call.context)

    hass.services.async_register("switch", "turn_on", turn_on)
    hass.services.async_register("switch", "turn_off", AsyncMock())
    with patch.object(publisher.delivery, "drain", AsyncMock()):
        task = asyncio.create_task(publisher.sample())
        await entered.wait()
        await hass.services.async_call(
            "switch", "turn_off", {"entity_id": "switch.second"}, blocking=True
        )
        release.set()
        await task
        await hass.async_block_till_done()
    assert len(calls) == 1
    assert hass.states.get("switch.second").state == "off"
    assert publisher.data["overrides"]["binding-1:second"]


async def test_config_outbox_interrupted_write_recovers_on_exact_retry(hass, energy):
    publisher = await configure(energy)
    msg = message(energy, revision=1)
    with (
        patch.object(
            publisher.delivery.store, "async_save", AsyncMock(side_effect=OSError)
        ),
        pytest.raises(OSError),
    ):
        await energy.update(msg, "save")
    assert (
        publisher.data["revision"] == 2
    )  # durable config committed before outbox reset
    assert publisher.delivery.data["configRevision"] == 1
    await energy.update(msg, "save")
    assert publisher.delivery.data["configRevision"] == 2
    restored = EnergyRuntime(hass)
    await restored.load()
    assert restored.publishers["account-a"].delivery.data["configRevision"] == 2


@pytest.mark.parametrize(
    "status,error",
    [
        (401, None),
        (403, None),
        (202, None),
        (409, "binding_obsolete"),
        (409, "revision_obsolete"),
    ],
)
@pytest.mark.parametrize("operation", ("configure", "save"))
async def test_late_response_cannot_revoke_or_renew_rotated_configuration(
    hass, energy, status, error, operation
):
    publisher = await configure(energy)
    entered, release = asyncio.Event(), asyncio.Event()

    @asynccontextmanager
    async def delayed_post(*_args, **_kwargs):
        entered.set()
        await release.wait()
        yield SimpleNamespace(
            status=status, headers={}, json=AsyncMock(return_value={"error": error})
        )

    session = SimpleNamespace(post=delayed_post)
    with patch(
        "custom_components.hombee.energy.delivery.async_get_clientsession",
        return_value=session,
    ):
        sample_task = asyncio.create_task(publisher.sample())
        await entered.wait()
        msg = message(
            energy,
            revision=1,
            publisher={
                "url": "https://hombee.app",
                "token": "n" * 72,
                "generation": "new-generation",
                "bindingRevision": 2,
            },
        )
        if operation == "save":
            msg.pop("publisher")
        saved = asyncio.Event()
        original_save = publisher.store.async_save

        async def record_save(data):
            await original_save(data)
            saved.set()

        with patch.object(publisher.store, "async_save", side_effect=record_save):
            configure_task = asyncio.create_task(energy.update(msg, operation))
            async with asyncio.timeout(2):
                await saved.wait()
            assert publisher.data["publisher"]["bindingRevision"] == 2
            release.set()
            await sample_task
            await configure_task
    assert not publisher.delivery.data["revoked"]
    assert not publisher.delivery.data["bindings"]["binding-1"]["revoked"]
    assert publisher.authorization_reason("binding-1") is None
    assert publisher.delivery.data["configRevision"] == 2
    assert publisher.delivery.diagnostic is None


async def test_binding_rejection_and_lease_do_not_affect_other_site(
    hass, energy, aioclient_mock, freezer
):
    entry = MockConfigEntry(
        domain="solaredge_modbus", unique_id="INV-SECOND", title="Second inverter"
    )
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("solaredge_modbus", "INV-SECOND")}
    )
    entity = er.async_get(hass).async_get_or_create(
        "sensor",
        "solaredge_modbus",
        "INV-SECOND_ac_power",
        config_entry=entry,
        device_id=device.id,
    )
    hass.states.async_set(entity.entity_id, "1000", {"unit_of_measurement": "W"})
    second = BINDING(
        {
            "bindingId": "second-binding",
            "site": {"provider": "solaredge", "id": "second-site"},
            "sourceIds": [entry.entry_id],
        }
    )
    publisher = await configure(energy, bindings=[energy.test_binding, second])
    first_lease = publisher.delivery.data["bindings"]["binding-1"]["authorizedUntil"]
    url = f"https://hombee.app/api/energy/local/instances/{energy.instance_id}/observations"
    rejected = SimpleNamespace(
        status=409,
        headers={},
        json=AsyncMock(return_value={"error": "binding_obsolete"}),
    )
    accepted = SimpleNamespace(status=202, headers={})

    @asynccontextmanager
    async def post(_url, **kwargs):
        yield rejected if kwargs["json"]["bindingId"] == "binding-1" else accepted

    freezer.tick(10)
    with patch(
        "custom_components.hombee.energy.delivery.async_get_clientsession",
        return_value=SimpleNamespace(post=post),
    ):
        await publisher.sample()
    assert (
        publisher.authorization_reason("binding-1") == "binding_authorization_required"
    )
    assert publisher.authorization_reason("second-binding") is None
    assert not publisher.delivery.data["revoked"]
    assert publisher.delivery.data["bindings"]["binding-1"]["authorizedUntil"] == 0
    assert (
        publisher.delivery.data["bindings"]["second-binding"]["authorizedUntil"]
        > first_lease
    )
    # Even accepted activity for the remaining site cannot grant the old site
    # a new offline authorization interval.
    publisher.delivery.data["bindings"]["binding-1"] = {
        "authorizedUntil": first_lease,
        "revoked": False,
    }
    freezer.tick(24 * 60 * 60 - 5)
    for state in hass.states.async_all():
        hass.states.async_set(state.entity_id, state.state, state.attributes)
    await publisher.delivery.enqueue([observation(hass, second, discover(hass))])
    aioclient_mock.post(url, status=202)
    await publisher.delivery.drain()
    assert publisher.authorization_reason("binding-1") == "policy_authorization_expired"
    assert publisher.authorization_reason("second-binding") is None
