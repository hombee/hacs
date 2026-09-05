"""Runs the managed-light acceptance contract inside Home Assistant."""

from __future__ import annotations

import json
import traceback
from pathlib import Path

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import ATTR_ENTITY_ID, EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er

from custom_components.hombee_air.const import DOMAIN as HOMBEE_DOMAIN
from custom_components.hombee_air.managed_lighting import (
    ManagedLightingManager,
    ManagedLightSpec,
)

DOMAIN = "hombee_managed_light_test"
PUBLIC_ENTITY_ID = "light.contract_physical"
RESULT_PATH = Path("/config/managed-light-result.json")


async def async_setup(hass: HomeAssistant, _config: dict) -> bool:
    """Schedules the contract after Home Assistant starts."""
    hass.data[DOMAIN] = {"turn_on_calls": []}

    @callback
    def start_contract(_event) -> None:
        hass.async_create_background_task(
            _async_run_contract(hass), "Hombee managed light Docker contract"
        )

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, start_contract)
    return True


async def _async_run_contract(hass: HomeAssistant) -> None:
    try:
        await _async_assert_contract(hass)
        await hass.async_add_executor_job(_write_result, {"status": "passed"})
    except (
        AssertionError,
        KeyError,
        RuntimeError,
        StopIteration,
        TypeError,
        ValueError,
    ) as error:
        await hass.async_add_executor_job(
            _write_result,
            {
                "status": "failed",
                "error": str(error),
                "traceback": traceback.format_exc(),
            },
        )
    finally:
        hass.stop()


async def _async_assert_contract(hass: HomeAssistant) -> None:
    registry = er.async_get(hass)
    source_entry = registry.async_get(PUBLIC_ENTITY_ID)
    assert source_entry is not None
    area = ar.async_get(hass).async_get_or_create("Contract kitchen")
    registry.async_update_entity(
        PUBLIC_ENTITY_ID,
        area_id=area.id,
        labels={"contract-lighting"},
    )

    result = await hass.config_entries.flow.async_init(
        HOMBEE_DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "managed_lighting"}
    )
    entry = result["result"]
    await hass.async_block_till_done()
    manager = entry.runtime_data
    assert isinstance(manager, ManagedLightingManager)

    spec = ManagedLightSpec(
        source_entity_id=PUBLIC_ENTITY_ID,
        warm_kelvin=2200,
        cool_kelvin=5000,
        transition_seconds=1,
    )
    await manager.async_reconcile(1, [spec])

    logical_entry = registry.async_get(PUBLIC_ENTITY_ID)
    assert logical_entry is not None
    assert logical_entry.platform == HOMBEE_DOMAIN
    assert logical_entry.area_id == area.id
    assert logical_entry.device_id == source_entry.device_id
    assert logical_entry.labels == {"contract-lighting"}

    physical_entry = next(
        entry for entry in registry.entities.values() if entry.id == source_entry.id
    )
    assert physical_entry.entity_id == "light.contract_physical_physical"
    assert physical_entry.hidden_by is er.RegistryEntryHider.INTEGRATION

    hass.states.async_set("sun.sun", "below_horizon", {"rising": False})
    calls = hass.data[DOMAIN]["turn_on_calls"]
    await hass.services.async_call(
        "light",
        "turn_on",
        {"brightness": 128},
        target={ATTR_ENTITY_ID: PUBLIC_ENTITY_ID},
        blocking=True,
    )
    assert calls == [{"brightness": 128, "color_temp_kelvin": 2200}]

    await hass.services.async_call(
        "light",
        "turn_off",
        target={ATTR_ENTITY_ID: PUBLIC_ENTITY_ID},
        blocking=True,
    )
    calls.clear()
    await hass.services.async_call(
        "light",
        "turn_on",
        {"brightness": 64},
        target={"area_id": area.id},
        blocking=True,
    )
    assert calls == [{"brightness": 64, "color_temp_kelvin": 2200}]

    calls.clear()
    await hass.services.async_call(
        "light",
        "turn_on",
        {"color_temp_kelvin": 4000},
        target={ATTR_ENTITY_ID: PUBLIC_ENTITY_ID},
        blocking=True,
    )
    assert calls == [{"color_temp_kelvin": 4000}]
    logical = manager.entities[source_entry.id]
    await logical.async_reconcile_temperature()
    assert calls == [{"color_temp_kelvin": 4000}]

    await hass.services.async_call(
        "light",
        "turn_off",
        target={ATTR_ENTITY_ID: PUBLIC_ENTITY_ID},
        blocking=True,
    )
    calls.clear()
    await hass.services.async_call(
        "light",
        "turn_on",
        target={ATTR_ENTITY_ID: PUBLIC_ENTITY_ID},
        blocking=True,
    )
    assert calls == [{"color_temp_kelvin": 2200}]

    await manager.async_reconcile(1, [spec])
    assert len(manager.entities) == 1
    await manager.async_reconcile(2, [])
    restored_entry = registry.async_get(PUBLIC_ENTITY_ID)
    assert restored_entry is not None
    assert restored_entry.id == source_entry.id
    assert restored_entry.hidden_by is None


def _write_result(result: dict) -> None:
    RESULT_PATH.write_text(json.dumps(result, indent=2), encoding="utf-8")
