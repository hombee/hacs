"""Tests for the Hombee Air climate entity and write services."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hombee_air.const import DOMAIN
from custom_components.hombee_air.modbus_client import HombeeAirModbusError
from custom_components.hombee_air.registers import HombeeAirRegister

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
        self.raw[register.key] = raw_value
        future.set_result(None)
        return future


@pytest.fixture
async def mock_client(hass: HomeAssistant) -> MockModbusClient:
    client = MockModbusClient("127.0.0.1", 502)
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
    return client


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
