"""Authenticated Hombee cloud client and Assist entity identity."""

from __future__ import annotations

import asyncio

from homeassistant.components.assist_pipeline import pipeline
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import Entity

from .assist_client import HombeeAssistClient

ASSIST_ENTRY_KIND = "assist"
ASSIST_PROTOCOL = 2
ASSIST_NAME = "Hombee Voice"
ASSIST_LANGUAGES = ["en", "en-US", "en-GB", "pl", "pl-PL"]


class HombeeAssistEntity(Entity):
    """Shared identity for the three native Assist stages."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, stage: str) -> None:
        self.entry = entry
        self.client: HombeeAssistClient = entry.runtime_data
        self._attr_unique_id = f"hombee_assist_{stage}"
        self._attr_name = f"{ASSIST_NAME} {stage}"


async def async_setup_assist_pipeline(
    hass: HomeAssistant, entry: ConfigEntry, *, repair=False
) -> None:
    """Create one pipeline after platform entities have registered."""
    await asyncio.sleep(0)
    registry = er.async_get(hass)
    stt_id = registry.async_get_entity_id(
        "stt", "hombee", "hombee_assist_transcription"
    )
    tts_id = registry.async_get_entity_id("tts", "hombee", "hombee_assist_speech")
    conversation_id = registry.async_get_entity_id(
        "conversation", "hombee", "hombee_assist_conversation"
    )
    if not stt_id or not tts_id or not conversation_id:
        raise HomeAssistantError("Hombee Voice entities did not finish loading.")
    existing = next(
        (
            item
            for item in pipeline.async_get_pipelines(hass)
            if item.id == entry.options.get("pipeline_id")
        ),
        None,
    )
    if existing is not None:
        if repair:
            await pipeline.async_update_pipeline(
                hass,
                existing,
                stt_engine=stt_id,
                tts_engine=tts_id,
                conversation_engine=conversation_id,
            )
        return
    created = await pipeline.async_create_default_pipeline(
        hass, stt_id, tts_id, ASSIST_NAME
    )
    if created is None:
        raise HomeAssistantError("Choose English or Polish as your Assist language.")
    await pipeline.async_update_pipeline(
        hass, created, conversation_engine=conversation_id, prefer_local_intents=True
    )
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "pipeline_id": created.id}
    )


def assist_pipeline_ready(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Check actual pipeline providers without changing the user's settings."""
    registry = er.async_get(hass)
    configured = next(
        (
            item
            for item in pipeline.async_get_pipelines(hass)
            if item.id == entry.options.get("pipeline_id")
        ),
        None,
    )
    if configured is None:
        return False
    return all(
        engine
        == registry.async_get_entity_id(domain, "hombee", f"hombee_assist_{stage}")
        and engine is not None
        for engine, domain, stage in (
            (configured.stt_engine, "stt", "transcription"),
            (configured.tts_engine, "tts", "speech"),
            (configured.conversation_engine, "conversation", "conversation"),
        )
    )
