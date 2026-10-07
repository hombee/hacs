"""Exercise exposure of real transient scene entities in Home Assistant."""

from homeassistant.components.homeassistant.exposed_entities import (
    KNOWN_ASSISTANTS,
    async_expose_entity,
    async_should_expose,
)
from homeassistant.setup import async_setup_component

from custom_components.hombee.voice_exposure import async_setup_voice_exposure


async def test_restore_scenes_are_unexposed_on_setup_and_recreation(hass):
    """Existing and new snapshots stay usable by automation but absent from voice."""
    assert await async_setup_component(hass, "scene", {})
    assert await async_setup_component(hass, "light", {})
    hass.states.async_set("light.kitchen", "on", {"friendly_name": "Kitchen lamp"})

    async def turn_on(call):
        hass.states.async_set("light.kitchen", "on", {"friendly_name": "Kitchen lamp"})

    hass.services.async_register("light", "turn_on", turn_on)

    async def create(scene_id):
        await hass.services.async_call(
            "scene",
            "create",
            {"scene_id": scene_id, "snapshot_entities": ["light.kitchen"]},
            blocking=True,
        )
        await hass.async_block_till_done()

    existing = "scene.hombee_restore_existing_toggle"
    await create(existing.split(".", 1)[1])
    for assistant in KNOWN_ASSISTANTS:
        async_expose_entity(hass, assistant, existing, True)
    assert await async_setup_component(hass, "hombee", {})
    await hass.async_block_till_done()
    for assistant in KNOWN_ASSISTANTS:
        assert not async_should_expose(hass, assistant, existing)

    created = "scene.hombee_restore_new_toggle"
    await create(created.split(".", 1)[1])
    for assistant in KNOWN_ASSISTANTS:
        assert not async_should_expose(hass, assistant, created)
        async_expose_entity(hass, assistant, created, True)
    await create(created.split(".", 1)[1])
    for assistant in KNOWN_ASSISTANTS:
        assert not async_should_expose(hass, assistant, created)

    hass.states.async_set("light.kitchen", "off", {"friendly_name": "Kitchen lamp"})
    # Restore scenes remain callable by existing automations.
    await hass.services.async_call(
        "scene", "turn_on", {"entity_id": created}, blocking=True
    )
    assert hass.states.get(created) is not None
    assert hass.states.is_state("light.kitchen", "on")


async def test_user_scene_exposure_is_unchanged_and_setup_is_idempotent(hass):
    """The policy is scoped to Hombee snapshot IDs, not all scene entities."""
    hass.states.async_set("scene.dinner", "unknown", {"friendly_name": "Dinner"})
    async_expose_entity(hass, "conversation", "scene.dinner", True)
    async_setup_voice_exposure(hass)
    listener_count = hass.bus.async_listeners()["state_changed"]
    async_setup_voice_exposure(hass)
    assert hass.bus.async_listeners()["state_changed"] == listener_count
    hass.states.async_set("scene.dinner", "2026-10-07T12:00:00+00:00")
    await hass.async_block_till_done()
    assert async_should_expose(hass, "conversation", "scene.dinner")
