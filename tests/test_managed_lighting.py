"""Policy and entity registration tests for Hombee managed lighting."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from homeassistant.components.light import ColorMode, LightEntity, LightEntityFeature
from homeassistant.config_entries import ConfigFlow
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
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


async def _setup_source_light(
    hass: HomeAssistant,
    with_device: bool,
    mode: ColorMode = ColorMode.COLOR_TEMP,
    extra_lights: tuple = (),
) -> RecordingLight:
    """Register a physical CCT light using Home Assistant's light platform."""

    async def setup_source(hass, entry):
        await hass.config_entries.async_forward_entry_setups(entry, ["light"])
        return True

    mock_integration(hass, MockModule("test", async_setup_entry=setup_source))
    mock_platform(hass, "test.config_flow")
    source = RecordingLight()
    source.entity_id = "light.kitchen"
    source._attr_unique_id = "kitchen"
    source._attr_name = "Kitchen"
    source._attr_should_poll = False
    source._attr_is_on = False
    source._attr_supported_color_modes = {mode}
    source._attr_color_mode = mode
    if with_device:
        source._attr_device_info = dr.DeviceInfo(
            identifiers={("test", "kitchen")}, name="Kitchen device"
        )
    setup_test_component_platform(
        hass, "light", [source, *extra_lights], from_config_entry=True
    )
    source_config = MockConfigEntry(domain="test")
    source_config.add_to_hass(hass)
    with mock_config_flow("test", ConfigFlow):
        assert await hass.config_entries.async_setup(source_config.entry_id)
    await hass.async_block_till_done()
    return source


class RecordingLight(LightEntity):
    """Physical lamp that records actual HA service payloads and reports state."""

    _attr_supported_features = LightEntityFeature.TRANSITION
    _attr_min_color_temp_kelvin = 2000
    _attr_max_color_temp_kelvin = 6500
    _attr_brightness = 200
    _attr_color_temp_kelvin = 4000

    def __init__(self):
        self.calls = []

    async def async_turn_on(self, **kwargs):
        self.calls.append(dict(kwargs))
        self._attr_is_on = True
        if "brightness" in kwargs:
            self._attr_brightness = kwargs["brightness"]
        if "color_temp_kelvin" in kwargs:
            self._attr_color_temp_kelvin = kwargs["color_temp_kelvin"]
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        self._attr_is_on = False
        self.async_write_ha_state()


async def _setup_managed(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=ENTRY_KIND_MANAGED_LIGHTING,
        data={CONF_ENTRY_KIND: ENTRY_KIND_MANAGED_LIGHTING},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _light_action(hass, service="turn_on", **data):
    await hass.services.async_call(
        "light", service, {"entity_id": "light.kitchen", **data}, blocking=True
    )
    await hass.async_block_till_done()


@pytest.mark.parametrize(
    "mode", [ColorMode.BRIGHTNESS, ColorMode.COLOR_TEMP, ColorMode.HS, ColorMode.RGBW]
)
async def test_first_command_includes_only_supported_automatic_attributes(hass, mode):
    source = await _setup_source_light(hass, False, mode)
    entry = await _setup_managed(hass)
    manager = entry.runtime_data
    await manager.async_set_activity("default", "night")
    await _light_action(hass)
    assert len(source.calls) == 1
    assert source.calls[0]["brightness"] == 13
    assert ("color_temp_kelvin" in source.calls[0]) == (mode == ColorMode.COLOR_TEMP)
    entity = next(iter(manager.entities.values()))
    assert not entity.mapping.brightness_override
    assert not entity.mapping.manual_override
    assert (
        hass.states.get("light.kitchen").attributes["brightness_control"] == "schedule"
    )


async def test_onoff_lights_are_not_wrapped(hass):
    await _setup_source_light(hass, False, ColorMode.ONOFF)
    entry = await _setup_managed(hass)
    assert not entry.runtime_data.mappings
    assert er.async_get(hass).async_get("light.kitchen").platform == "test"


async def test_manual_attributes_are_independent_and_persist_across_reload(hass):
    source = await _setup_source_light(hass, False)
    entry = await _setup_managed(hass)
    manager = entry.runtime_data
    await manager.async_set_activity("default", "night")
    await _light_action(hass, brightness=102)
    entity = next(iter(manager.entities.values()))
    assert entity.mapping.brightness_override
    assert not entity.mapping.manual_override
    assert source.calls[-1]["brightness"] == 102
    assert "color_temp_kelvin" in source.calls[-1]
    await _light_action(hass, color_temp_kelvin=3000)
    assert entity.mapping.manual_override
    assert "brightness" not in source.calls[-1]
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    entity = next(iter(entry.runtime_data.entities.values()))
    assert entity.mapping.brightness_override
    assert entity.mapping.manual_override
    await _light_action(hass, "turn_off")
    assert not entity.mapping.brightness_override
    assert not entity.mapping.manual_override
    await _light_action(hass)
    assert source.calls[-1]["brightness"] == 13


async def test_resume_attribute_and_global_switches_do_not_turn_on_lamps(hass):
    source = await _setup_source_light(hass, False)
    entry = await _setup_managed(hass)
    manager = entry.runtime_data
    await manager.async_set_enabled(False)
    await manager.async_set_brightness_enabled(False)
    await manager.async_set_activity("default", "night")
    await _light_action(hass, brightness=100, color_temp_kelvin=3500)
    entity = next(iter(manager.entities.values()))
    await hass.services.async_call(
        "hombee",
        "resume_adaptation",
        {
            "entity_id": "light.kitchen",
            "brightness": True,
            "color": False,
        },
        blocking=True,
    )
    assert not entity.mapping.brightness_override
    assert entity.mapping.manual_override
    await _light_action(hass, "turn_off")
    count = len(source.calls)
    await manager.async_set_enabled(True)
    await manager.async_set_brightness_enabled(True)
    assert len(source.calls) == count
    await manager.async_set_enabled(False)
    await _light_action(hass)
    assert source.calls[-1] == {"brightness": 13}


async def test_external_device_changes_pause_only_changed_attribute(hass, freezer):
    source = await _setup_source_light(hass, False)
    entry = await _setup_managed(hass)
    await _light_action(hass)
    freezer.tick(timedelta(seconds=10))
    source.async_set_context(Context())
    source._attr_brightness = 90
    source.async_write_ha_state()
    await hass.async_block_till_done()
    entity = next(iter(entry.runtime_data.entities.values()))
    assert entity.mapping.brightness_override
    assert not entity.mapping.manual_override
    source._attr_is_on = False
    source.async_write_ha_state()
    await hass.async_block_till_done()
    assert not entity.mapping.brightness_override


async def test_reconciliation_skips_unchanged_off_and_unavailable_lights(hass, freezer):
    source = await _setup_source_light(hass, False, ColorMode.BRIGHTNESS)
    entry = await _setup_managed(hass)
    manager = entry.runtime_data
    await manager.async_set_activity("default", "night")
    await _light_action(hass)
    freezer.tick(timedelta(seconds=65))
    await manager._async_reconcile_temperatures(datetime.now(UTC))
    assert len(source.calls) == 1
    source._attr_available = False
    source.async_write_ha_state()
    await hass.async_block_till_done()
    await manager.async_set_activity("default", "reading")
    assert len(source.calls) == 1
    source._attr_available = True
    source._attr_is_on = False
    source.async_write_ha_state()
    await hass.async_block_till_done()
    await manager._async_reconcile_temperatures(datetime.now(UTC))
    assert len(source.calls) == 1


async def test_lux_feedback_waits_for_fresh_samples_and_falls_back(hass, freezer):
    hass.states.async_set(
        "sensor.kitchen_lux",
        "800",
        {"device_class": "illuminance", "unit_of_measurement": "lx"},
    )
    source = await _setup_source_light(hass, False, ColorMode.BRIGHTNESS)
    area = ar.async_get(hass).async_create("Kitchen")
    er.async_get(hass).async_update_entity("light.kitchen", area_id=area.id)
    entry = await _setup_managed(hass)
    manager = entry.runtime_data
    await manager.async_configure_profile(
        area.id,
        {
            "illuminance_sensor": "sensor.kitchen_lux",
            "day_brightness": 50,
            "evening_brightness": 50,
            "night_brightness": 50,
            "day_lux": 300,
            "evening_lux": 300,
            "night_lux": 300,
        },
    )
    hass.states.async_set("sensor.kitchen_lux", "800", {"unit_of_measurement": "lx"})
    await _light_action(hass)
    assert source.calls[-1]["brightness"] == 128
    freezer.tick(timedelta(seconds=65))
    hass.states.async_set("sensor.kitchen_lux", "800", {"unit_of_measurement": "lx"})
    await manager._async_reconcile_temperatures(datetime.now(UTC))
    assert source.calls[-1]["brightness"] == 115
    count = len(source.calls)
    freezer.tick(timedelta(seconds=65))
    await manager._async_reconcile_temperatures(datetime.now(UTC))
    assert len(source.calls) == count
    hass.states.async_set("sensor.kitchen_lux", "800", {"unit_of_measurement": "lx"})
    await manager._async_reconcile_temperatures(datetime.now(UTC))
    assert source.calls[-1]["brightness"] == 102
    freezer.tick(timedelta(seconds=301))
    await manager._async_reconcile_temperatures(datetime.now(UTC))
    assert source.calls[-1]["brightness"] == 128
    assert (
        hass.states.get("light.kitchen").attributes["brightness_control"] == "schedule"
    )


async def test_options_validate_save_and_restore_room_profiles(hass):
    await _setup_source_light(hass, False)
    area = ar.async_get(hass).async_create("Kitchen")
    entry = await _setup_managed(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "room"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"area_id": area.id}
    )
    values = entry.runtime_data.profiles["default"].to_dict()
    values.pop("illuminance_sensor")
    values["wake_time"] = "23:00"
    values["sleep_time"] = "23:00"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], values
    )
    assert result["errors"] == {"base": "invalid_profile"}
    values["wake_time"] = "07:00"
    values["night_brightness"] = 7
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], values
    )
    assert result["type"] == "create_entry"
    assert entry.runtime_data.profiles[area.id].night_brightness == 7
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.profiles[area.id].night_brightness == 7


async def test_light_options_apply_limits_without_clamping_explicit_scene(hass):
    source = await _setup_source_light(hass, False)
    entry = await _setup_managed(hass)
    entity = next(iter(entry.runtime_data.entities.values()))
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "light"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"light": entity.mapping.source_registry_id}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "adapt_brightness": True,
            "adapt_color": False,
            "min_brightness": 20,
            "max_brightness": 60,
            "transition_seconds": 5,
        },
    )
    assert result["type"] == "create_entry"
    await entry.runtime_data.async_set_activity("default", "night")
    await _light_action(hass)
    assert source.calls[-1] == {"brightness": 51}
    await _light_action(hass, brightness=240, color_temp_kelvin=3100)
    assert source.calls[-1] == {"brightness": 240, "color_temp_kelvin": 3100}


async def test_off_wins_over_inflight_and_queued_reconciliation(
    hass, freezer, monkeypatch
):
    source = await _setup_source_light(hass, False, ColorMode.BRIGHTNESS)
    entry = await _setup_managed(hass)
    manager = entry.runtime_data
    await manager.async_set_activity("default", "night")
    await _light_action(hass)
    freezer.tick(timedelta(seconds=10))
    manager.activities["default"] = "reading"
    entity = next(iter(manager.entities.values()))
    original = source.async_turn_on
    started = asyncio.Event()
    release = asyncio.Event()

    async def delayed(**kwargs):
        started.set()
        await release.wait()
        await original(**kwargs)

    monkeypatch.setattr(source, "async_turn_on", delayed)
    correction = hass.async_create_task(entity.async_reconcile_temperature())
    await asyncio.wait_for(started.wait(), timeout=2)
    turn_off = hass.async_create_task(entity.async_turn_off())
    await asyncio.sleep(0)
    queued = hass.async_create_task(entity.async_reconcile_temperature())
    release.set()
    await asyncio.gather(correction, turn_off, queued)
    await hass.async_block_till_done()
    assert not source.is_on
    assert len(source.calls) == 2
    # A later physical turn-on can still be adapted.
    source._attr_is_on = True
    source._attr_brightness = 40
    source.async_set_context(Context())
    source.async_write_ha_state()
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=10))
    await entity.async_reconcile_temperature()
    assert source.calls[-1]["brightness"] == 255


async def test_failed_explicit_command_does_not_take_manual_control(hass, monkeypatch):
    source = await _setup_source_light(hass, False)
    entry = await _setup_managed(hass)

    async def failed(**kwargs):
        raise HomeAssistantError("Device disconnected")

    monkeypatch.setattr(source, "async_turn_on", failed)
    with pytest.raises(HomeAssistantError):
        await _light_action(hass, brightness=10, color_temp_kelvin=3000)
    mapping = next(iter(entry.runtime_data.mappings.values()))
    assert not mapping.brightness_override
    assert not mapping.manual_override


async def test_room_area_edits_and_activity_select_survive_reload(hass):
    await _setup_source_light(hass, False, ColorMode.BRIGHTNESS)
    entry = await _setup_managed(hass)
    area = ar.async_get(hass).async_create("Study")
    registry = er.async_get(hass)
    registry.async_update_entity("light.kitchen", area_id=area.id)
    await hass.async_block_till_done()
    selector_id = registry.async_get_entity_id(
        "select", DOMAIN, f"hombee_lighting_activity_{area.id}"
    )
    assert selector_id is not None
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": selector_id, "option": "reading"},
        blocking=True,
    )
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert registry.async_get("light.kitchen").area_id == area.id
    mapping = next(iter(entry.runtime_data.mappings.values()))
    assert entry.runtime_data.activity_for(mapping) == "reading"
    await entry.runtime_data.async_configure_profile(area.id, {"night_brightness": 9})
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": selector_id, "option": "inherit"},
        blocking=True,
    )
    await entry.runtime_data.async_set_activity("default", "night")
    assert entry.runtime_data.activity_for(mapping) == "night"
    assert entry.runtime_data.profiles[area.id].night_brightness == 9
    await entry.runtime_data.async_reset_profile(area.id)
    assert entry.runtime_data.activity_for(mapping) == "night"


async def test_lux_uses_one_correction_for_the_whole_room(hass, freezer):
    other = RecordingLight()
    other.entity_id = "light.desk"
    other._attr_unique_id = "desk"
    other._attr_name = "Desk"
    other._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
    other._attr_color_mode = ColorMode.BRIGHTNESS
    other._attr_is_on = False
    other._attr_should_poll = False
    source = await _setup_source_light(hass, False, ColorMode.BRIGHTNESS, (other,))
    area = ar.async_get(hass).async_create("Study")
    registry = er.async_get(hass)
    for light_id in ("light.kitchen", "light.desk"):
        registry.async_update_entity(light_id, area_id=area.id)
    entry = await _setup_managed(hass)
    manager = entry.runtime_data
    hass.states.async_set(
        "sensor.study_lux",
        "800",
        {"device_class": "illuminance", "unit_of_measurement": "lx"},
    )
    await manager.async_configure_profile(
        area.id,
        {
            "illuminance_sensor": "sensor.study_lux",
            "day_brightness": 50,
            "evening_brightness": 50,
            "night_brightness": 50,
        },
    )
    await hass.services.async_call(
        "light", "turn_on", {"area_id": area.id}, blocking=True
    )
    assert source.calls[-1]["brightness"] == other.calls[-1]["brightness"] == 128
    freezer.tick(timedelta(seconds=65))
    hass.states.async_set("sensor.study_lux", "5000", {"unit_of_measurement": "lx"})
    await manager._async_reconcile_temperatures(datetime.now(UTC))
    assert source.calls[-1]["brightness"] == other.calls[-1]["brightness"] == 115
    assert len(manager.lux_controllers) == 1


async def test_pre_turn_on_lux_report_is_not_used_as_feedback(hass, freezer):
    hass.states.async_set(
        "sensor.kitchen_lux",
        "0",
        {"device_class": "illuminance", "unit_of_measurement": "lx"},
    )
    source = await _setup_source_light(hass, False, ColorMode.BRIGHTNESS)
    area = ar.async_get(hass).async_create("Kitchen")
    er.async_get(hass).async_update_entity("light.kitchen", area_id=area.id)
    entry = await _setup_managed(hass)
    await entry.runtime_data.async_configure_profile(
        area.id,
        {
            "illuminance_sensor": "sensor.kitchen_lux",
            "night_brightness": 50,
        },
    )
    await entry.runtime_data.async_set_activity(area.id, "night")
    hass.states.async_set("sensor.kitchen_lux", "0", {"unit_of_measurement": "lx"})
    await _light_action(hass)
    freezer.tick(timedelta(seconds=65))
    await entry.runtime_data._async_reconcile_temperatures(datetime.now(UTC))
    assert len(source.calls) == 1
    assert source.calls[0]["brightness"] == 128


async def test_explicit_activity_and_resume_apply_without_waiting_for_timer(hass):
    source = await _setup_source_light(hass, False, ColorMode.BRIGHTNESS)
    entry = await _setup_managed(hass)
    manager = entry.runtime_data
    await manager.async_set_activity("default", "night")
    await _light_action(hass)
    assert source.calls[-1]["brightness"] == 13
    await manager.async_set_activity("default", "reading")
    assert source.calls[-1]["brightness"] == 255
    await _light_action(hass, brightness=80)
    await hass.services.async_call(
        "hombee",
        "resume_adaptation",
        {
            "entity_id": "light.kitchen",
            "brightness": True,
            "color": False,
        },
        blocking=True,
    )
    assert source.calls[-1]["brightness"] == 255
