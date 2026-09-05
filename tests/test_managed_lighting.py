"""Policy and entity registration tests for Hombee managed lighting."""

from datetime import UTC, datetime, timedelta

import pytest
from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.config_entries import ConfigFlow
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import label_registry as lr
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    MockModule,
    mock_config_flow,
    mock_integration,
    mock_platform,
    setup_test_component_platform,
)

from custom_components.hombee.const import (
    CONF_ENTRY_KIND,
    DOMAIN,
    ENTRY_KIND_MANAGED_LIGHTING,
)
from custom_components.hombee.managed_lighting import circadian_kelvin


@pytest.mark.parametrize(
    ("above_horizon", "rising", "now_offset", "expected"),
    [
        (False, False, timedelta(hours=1), 2200),
        (True, True, timedelta(0), 2200),
        (True, True, timedelta(hours=3), 3600),
        (True, True, timedelta(hours=6), 5000),
        (True, False, timedelta(hours=9), 3600),
        (True, False, timedelta(hours=12), 2200),
    ],
)
def test_circadian_curve_matches_shared_solar_policy(
    above_horizon: bool,
    rising: bool,
    now_offset: timedelta,
    expected: int,
) -> None:
    sunrise = datetime(2026, 6, 1, 6, tzinfo=UTC)
    noon = sunrise + timedelta(hours=6)
    sunset = noon + timedelta(hours=6)

    assert (
        circadian_kelvin(
            now=sunrise + now_offset,
            above_horizon=above_horizon,
            rising=rising,
            next_rising=sunrise + timedelta(days=1),
            next_noon=noon if rising else noon + timedelta(days=1),
            next_setting=sunset,
            warm_kelvin=2200,
            cool_kelvin=5000,
        )
        == expected
    )


@pytest.mark.parametrize(
    ("with_device", "entity_area", "entity_labels", "device_labels"),
    [
        (True, False, False, True),
        (True, True, True, True),
        (True, False, True, False),
        (False, True, True, False),
        (False, False, False, False),
    ],
)
async def test_new_light_preserves_area_and_labels(
    hass: HomeAssistant,
    with_device: bool,
    entity_area: bool,
    entity_labels: bool,
    device_labels: bool,
) -> None:
    """Preserve entity and device metadata through actual platform registration."""
    await _setup_source_light(hass, with_device)
    registry = er.async_get(hass)
    areas = ar.async_get(hass)
    labels = lr.async_get(hass)
    device_area = areas.async_create("Kitchen")
    override_area = areas.async_create("Dining room")
    entity_label = labels.async_create("Task lighting").label_id
    device_label = labels.async_create("Downstairs").label_id
    shared_label = labels.async_create("Lighting").label_id
    expected_labels = set()
    source_entry = registry.async_get("light.kitchen")
    assert source_entry is not None
    if with_device:
        dr.async_get(hass).async_update_device(
            source_entry.device_id,
            area_id=device_area.id,
            labels={device_label, shared_label} if device_labels else set(),
        )
        if device_labels:
            expected_labels.update({device_label, shared_label})
    if entity_labels:
        expected_labels.update({entity_label, shared_label})
    source_entry = registry.async_update_entity(
        source_entry.entity_id,
        area_id=override_area.id if entity_area else None,
        labels={entity_label, shared_label} if entity_labels else set(),
        name="Kitchen ceiling",
    )
    await hass.async_block_till_done()

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=ENTRY_KIND_MANAGED_LIGHTING,
        data={CONF_ENTRY_KIND: ENTRY_KIND_MANAGED_LIGHTING},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    expected_area = (
        override_area.id if entity_area else device_area.id if with_device else None
    )
    logical = registry.async_get("light.kitchen")
    assert logical is not None
    assert logical.platform == DOMAIN
    assert logical.area_id == expected_area
    assert logical.labels == expected_labels
    assert hass.states.get(logical.entity_id).attributes["friendly_name"] == (
        "Kitchen ceiling"
    )
    physical = registry.async_get("light.kitchen_physical")
    assert physical is not None
    assert physical.area_id == source_entry.area_id
    assert physical.labels == source_entry.labels
    assert physical.device_id == source_entry.device_id

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    restored = registry.async_get("light.kitchen")
    assert restored.area_id == expected_area
    assert restored.labels == expected_labels


async def _setup_source_light(hass: HomeAssistant, with_device: bool) -> None:
    """Register a physical CCT light using Home Assistant's light platform."""

    async def setup_source(hass, entry):
        await hass.config_entries.async_forward_entry_setups(entry, ["light"])
        return True

    mock_integration(hass, MockModule("test", async_setup_entry=setup_source))
    mock_platform(hass, "test.config_flow")
    source = LightEntity()
    source.entity_id = "light.kitchen"
    source._attr_unique_id = "kitchen"
    source._attr_name = "Kitchen"
    source._attr_should_poll = False
    source._attr_is_on = False
    source._attr_supported_color_modes = {ColorMode.COLOR_TEMP}
    source._attr_color_mode = ColorMode.COLOR_TEMP
    if with_device:
        source._attr_device_info = dr.DeviceInfo(
            identifiers={("test", "kitchen")}, name="Kitchen device"
        )
    setup_test_component_platform(hass, "light", [source], from_config_entry=True)
    source_config = MockConfigEntry(domain="test")
    source_config.add_to_hass(hass)
    with mock_config_flow("test", ConfigFlow):
        assert await hass.config_entries.async_setup(source_config.entry_id)
    await hass.async_block_till_done()
