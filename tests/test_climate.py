"""Tests for the Hombee Air climate entity and write services."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hombee_air.const import DOMAIN
from custom_components.hombee_air.modbus_client import HombeeAirModbusError
from custom_components.hombee_air.registers import REGISTERS_BY_KEY, HombeeAirRegister
from custom_components.hombee_air.repairs import active_alarm_issue_id

CLIMATE_ENTITY = "climate.hombee_air"

_BASE_RAW: dict[str, int | bool] = {
    "room_temperature": 219,
    "room_humidity": 455,
    "current_temperature_setpoint": 240,
    "current_humidity_setpoint": 500,
    "current_fan_gear": 2,
    "current_program": 3,
    "program_mode": 3,
    "msk_heat_img": True,
    "msk_cool_img": False,
    "msk_dehum_img": False,
    "fans_running": True,
}


class MockModbusClient:
    """In-memory stand-in for the paced Modbus client."""

    def __init__(self, host: str, port: int) -> None:
        self.raw: dict[str, int | bool] = dict(_BASE_RAW)
        self.writes: list[tuple[str, int]] = []
        self.fail_writes = False
        self.reflect_writes = True

    async def async_connect(self) -> None:
        pass

    async def async_close(self) -> None:
        pass

    async def async_read(self, plan: Any) -> dict[str, int | bool]:
        return {
            point.key: self.raw.get(point.key, 0)
            for chunk in plan
            for point in chunk.points
        }

    def async_write(
        self, register: HombeeAirRegister, raw_value: int
    ) -> asyncio.Future[None]:
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        if self.fail_writes:
            future.set_exception(HombeeAirModbusError("write failed"))
            return future
        self.writes.append((register.key, raw_value))
        if self.reflect_writes:
            self.raw[register.key] = raw_value
        future.set_result(None)
        return future


@pytest.fixture
async def mock_client(hass: HomeAssistant) -> MockModbusClient:
    client, _entry = await _setup_mock_client(hass)
    return client


async def _setup_mock_client(
    hass: HomeAssistant, raw_overrides: dict[str, int | bool] | None = None
) -> tuple[MockModbusClient, MockConfigEntry]:
    client = MockModbusClient("127.0.0.1", 502)
    if raw_overrides is not None:
        client.raw.update(raw_overrides)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="test_unit",
        title="Hombee Air",
        data={
            "host": "127.0.0.1",
            "name": "Hombee Air",
            "installation_id": "test-unit",
            "port": 502,
        },
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.hombee_air.HombeeAirModbusClient",
        return_value=client,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return client, entry


async def test_climate_state_reflects_registers(
    hass: HomeAssistant, mock_client: MockModbusClient
) -> None:
    state = hass.states.get(CLIMATE_ENTITY)
    assert state is not None
    assert state.state == "auto"
    assert state.attributes["current_temperature"] == 21.9
    assert state.attributes["current_humidity"] == 45.5
    assert state.attributes["temperature"] == 24.0
    assert state.attributes["target_temp_step"] == 0.1
    assert state.attributes["humidity"] == 50.0
    assert state.attributes["hvac_modes"] == ["auto"]
    assert state.attributes["preset_modes"] == [
        "off",
        "economy",
        "comfort",
        "comfort_plus",
        "manual",
    ]
    assert state.attributes["fan_mode"] == "2"
    assert state.attributes["preset_mode"] == "comfort"
    assert state.attributes["hvac_action"] == "heating"


async def test_set_temperature_writes_active_preset_pair(
    hass: HomeAssistant, mock_client: MockModbusClient
) -> None:
    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": CLIMATE_ENTITY, "temperature": 25.1},
        blocking=True,
    )
    assert ("comfort_heating_temperature_setpoint", 251) in mock_client.writes
    assert ("comfort_cooling_temperature_setpoint", 251) in mock_client.writes
    state = hass.states.get(CLIMATE_ENTITY)
    assert state.attributes["temperature"] == 25.1


async def test_set_temperature_survives_stale_immediate_readback(
    hass: HomeAssistant, mock_client: MockModbusClient
) -> None:
    mock_client.reflect_writes = False
    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": CLIMATE_ENTITY, "temperature": 25.1},
        blocking=True,
    )
    assert mock_client.raw["current_temperature_setpoint"] == 240
    state = hass.states.get(CLIMATE_ENTITY)
    assert state.attributes["temperature"] == 25.1


async def test_set_humidity_writes_active_preset_pair(
    hass: HomeAssistant, mock_client: MockModbusClient
) -> None:
    await hass.services.async_call(
        "climate",
        "set_humidity",
        {"entity_id": CLIMATE_ENTITY, "humidity": 55},
        blocking=True,
    )
    assert ("comfort_heating_humidity_setpoint", 550) in mock_client.writes
    assert ("comfort_cooling_humidity_setpoint", 550) in mock_client.writes


async def test_set_humidity_survives_stale_immediate_readback(
    hass: HomeAssistant, mock_client: MockModbusClient
) -> None:
    mock_client.reflect_writes = False
    await hass.services.async_call(
        "climate",
        "set_humidity",
        {"entity_id": CLIMATE_ENTITY, "humidity": 55},
        blocking=True,
    )
    assert mock_client.raw["current_humidity_setpoint"] == 500
    state = hass.states.get(CLIMATE_ENTITY)
    assert state.attributes["humidity"] == 55


async def test_set_preset_mode_writes_program(
    hass: HomeAssistant, mock_client: MockModbusClient
) -> None:
    await hass.services.async_call(
        "climate",
        "set_preset_mode",
        {"entity_id": CLIMATE_ENTITY, "preset_mode": "economy"},
        blocking=True,
    )
    assert ("program_mode", 2) in mock_client.writes
    state = hass.states.get(CLIMATE_ENTITY)
    assert state.attributes["preset_mode"] == "economy"


async def test_set_preset_mode_survives_stale_immediate_readback(
    hass: HomeAssistant, mock_client: MockModbusClient
) -> None:
    mock_client.reflect_writes = False
    await hass.services.async_call(
        "climate",
        "set_preset_mode",
        {"entity_id": CLIMATE_ENTITY, "preset_mode": "economy"},
        blocking=True,
    )
    assert mock_client.raw["program_mode"] == 3
    state = hass.states.get(CLIMATE_ENTITY)
    assert state.attributes["preset_mode"] == "economy"


async def test_preset_mode_off_writes_program_off(
    hass: HomeAssistant, mock_client: MockModbusClient
) -> None:
    await hass.services.async_call(
        "climate",
        "set_preset_mode",
        {"entity_id": CLIMATE_ENTITY, "preset_mode": "off"},
        blocking=True,
    )
    assert ("program_mode", 0) in mock_client.writes
    assert hass.states.get(CLIMATE_ENTITY).state == "off"


async def test_set_temperature_requires_active_mode(
    hass: HomeAssistant, mock_client: MockModbusClient
) -> None:
    await hass.services.async_call(
        "climate",
        "set_preset_mode",
        {"entity_id": CLIMATE_ENTITY, "preset_mode": "off"},
        blocking=True,
    )

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "climate",
            "set_temperature",
            {"entity_id": CLIMATE_ENTITY, "temperature": 22.0},
            blocking=True,
        )

    assert ("comfort_heating_temperature_setpoint", 220) not in mock_client.writes


async def test_turn_off_and_on_restores_program(
    hass: HomeAssistant, mock_client: MockModbusClient
) -> None:
    await hass.services.async_call(
        "climate",
        "turn_off",
        {"entity_id": CLIMATE_ENTITY},
        blocking=True,
    )
    assert ("program_mode", 0) in mock_client.writes
    assert hass.states.get(CLIMATE_ENTITY).state == "off"
    await hass.services.async_call(
        "climate",
        "turn_on",
        {"entity_id": CLIMATE_ENTITY},
        blocking=True,
    )
    assert ("program_mode", 3) in mock_client.writes
    assert hass.states.get(CLIMATE_ENTITY).state == "auto"


async def test_set_fan_mode_writes_active_preset_gear(
    hass: HomeAssistant, mock_client: MockModbusClient
) -> None:
    await hass.services.async_call(
        "climate",
        "set_fan_mode",
        {"entity_id": CLIMATE_ENTITY, "fan_mode": "3"},
        blocking=True,
    )
    assert ("comfort_program_fan_gear", 3) in mock_client.writes
    state = hass.states.get(CLIMATE_ENTITY)
    assert state.attributes["fan_mode"] == "3"


async def test_set_fan_mode_survives_stale_immediate_readback(
    hass: HomeAssistant, mock_client: MockModbusClient
) -> None:
    mock_client.reflect_writes = False
    await hass.services.async_call(
        "climate",
        "set_fan_mode",
        {"entity_id": CLIMATE_ENTITY, "fan_mode": "3"},
        blocking=True,
    )
    assert mock_client.raw["current_fan_gear"] == 2
    state = hass.states.get(CLIMATE_ENTITY)
    assert state.attributes["fan_mode"] == "3"


async def test_number_entity_survives_stale_immediate_readback(
    hass: HomeAssistant, mock_client: MockModbusClient
) -> None:
    mock_client.reflect_writes = False
    entity_id = _entity_id_for_unique_id(
        hass, "hombee_air_test_unit_comfort_heating_temperature_setpoint"
    )

    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": entity_id, "value": 25.1},
        blocking=True,
    )

    assert "comfort_heating_temperature_setpoint" not in mock_client.raw
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "25.1"


@pytest.mark.parametrize(
    ("domain", "key"),
    [
        ("sensor", "current_program"),
        ("binary_sensor", "alarm_room_temp_prb"),
        ("number", "comfort_heating_temperature_setpoint"),
        ("select", "manual_supply_fan_gear"),
        ("switch", "alarm_reset_by_bms"),
    ],
)
async def test_register_entities_use_native_translation_keys(
    hass: HomeAssistant,
    mock_client: MockModbusClient,
    domain: str,
    key: str,
) -> None:
    entity_id = _entity_id_for_domain_and_unique_id(
        hass, domain, f"hombee_air_test_unit_{key}"
    )
    registry_entry = er.async_get(hass).async_get(entity_id)

    assert registry_entry is not None
    assert registry_entry.translation_key == key


async def test_enum_options_use_translation_keys_and_accept_legacy_labels(
    hass: HomeAssistant, mock_client: MockModbusClient
) -> None:
    sensor_id = _entity_id_for_domain_and_unique_id(
        hass, "sensor", "hombee_air_test_unit_current_program"
    )
    select_id = _entity_id_for_domain_and_unique_id(
        hass, "select", "hombee_air_test_unit_program_mode"
    )

    sensor_state = hass.states.get(sensor_id)
    select_state = hass.states.get(select_id)
    assert sensor_state is not None
    assert select_state is not None
    assert sensor_state.state == "comfort"
    assert select_state.state == "comfort"
    assert select_state.attributes["options"] == [
        "off",
        "standby",
        "economy",
        "comfort",
        "comfort_plus",
        "auto",
        "manual",
    ]

    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": select_id, "option": "economy"},
        blocking=True,
    )
    assert ("program_mode", 2) in mock_client.writes

    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": select_id, "option": "Comfort+"},
        blocking=True,
    )
    assert ("program_mode", 4) in mock_client.writes


async def test_failed_write_rolls_back_optimistic_state(
    hass: HomeAssistant, mock_client: MockModbusClient
) -> None:
    mock_client.fail_writes = True
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "climate",
            "set_temperature",
            {"entity_id": CLIMATE_ENTITY, "temperature": 30.0},
            blocking=True,
        )
    state = hass.states.get(CLIMATE_ENTITY)
    assert state.attributes["temperature"] == 24.0


async def test_write_register_service_validates_catalog(
    hass: HomeAssistant, mock_client: MockModbusClient
) -> None:
    await hass.services.async_call(
        DOMAIN,
        "write_register",
        {
            "installation_id": "test-unit",
            "key": "manual_temperature_setpoint",
            "value": 230,
        },
        blocking=True,
    )
    assert ("manual_temperature_setpoint", 230) in mock_client.writes
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN,
            "write_register",
            {
                "installation_id": "test-unit",
                "key": "room_temperature",
                "value": 230,
            },
            blocking=True,
        )


async def test_alarm_binary_sensor_is_diagnostic_problem(
    hass: HomeAssistant, mock_client: MockModbusClient
) -> None:
    registry = er.async_get(hass)
    alarm_entries = [
        entry
        for entry in registry.entities.values()
        if entry.platform == DOMAIN
        and entry.domain == "binary_sensor"
        and entry.entity_category is not None
    ]
    assert alarm_entries


async def test_active_alarm_creates_repair_issue_on_setup(
    hass: HomeAssistant,
) -> None:
    await _setup_mock_client(hass, {"alarm_room_temp_prb": True})

    issue = _alarm_issue(hass, "alarm_room_temp_prb")
    assert issue is not None
    assert issue.severity == ir.IssueSeverity.WARNING
    assert issue.translation_key == "active_alarm"
    assert issue.is_fixable is False
    assert issue.is_persistent is False
    assert issue.translation_placeholders == {
        "unit": "Hombee Air",
        "alarm_code": "A01",
    }


async def test_alarm_repair_issue_clears_when_alarm_clears(
    hass: HomeAssistant,
) -> None:
    client, entry = await _setup_mock_client(hass, {"alarm_room_temp_prb": True})
    assert _alarm_issue(hass, "alarm_room_temp_prb") is not None

    client.raw["alarm_room_temp_prb"] = False
    await entry.runtime_data.fast.async_refresh()
    await hass.async_block_till_done()

    assert _alarm_issue(hass, "alarm_room_temp_prb") is None


async def test_multiple_active_alarms_create_separate_repair_issues(
    hass: HomeAssistant,
) -> None:
    await _setup_mock_client(
        hass,
        {
            "alarm_room_temp_prb": True,
            "alarm_ret_temp_prb": True,
        },
    )

    assert _alarm_issue(hass, "alarm_room_temp_prb") is not None
    assert _alarm_issue(hass, "alarm_ret_temp_prb") is not None


async def test_noncoded_alarm_boolean_does_not_create_repair_issue(
    hass: HomeAssistant,
) -> None:
    await _setup_mock_client(hass, {"alarm_reset_by_bms": True})

    assert _alarm_issue(hass, "alarm_reset_by_bms") is None


async def test_alarm_repair_issues_clear_on_unload(
    hass: HomeAssistant,
) -> None:
    _client, entry = await _setup_mock_client(hass, {"alarm_room_temp_prb": True})
    assert _alarm_issue(hass, "alarm_room_temp_prb") is not None

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert _alarm_issue(hass, "alarm_room_temp_prb") is None


def _entity_id_for_unique_id(hass: HomeAssistant, unique_id: str) -> str:
    return _entity_id_for_domain_and_unique_id(hass, "number", unique_id)


def _entity_id_for_domain_and_unique_id(
    hass: HomeAssistant, domain: str, unique_id: str
) -> str:
    registry = er.async_get(hass)
    entry = registry.async_get_entity_id(domain, DOMAIN, unique_id)
    assert entry is not None
    return entry


def _alarm_issue(hass: HomeAssistant, key: str) -> ir.IssueEntry | None:
    register = REGISTERS_BY_KEY[key]
    return ir.async_get(hass).async_get_issue(
        DOMAIN, active_alarm_issue_id("test_unit", register)
    )
