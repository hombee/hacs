"""Authenticated Hombee cloud client and Assist entity identity."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

from aiohttp import ClientError, ClientTimeout
from homeassistant.components.assist_pipeline import pipeline
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity import Entity

ASSIST_ENTRY_KIND = "assist"
ASSIST_PROTOCOL = 1
ASSIST_NAME = "Hombee Voice"
ASSIST_LANGUAGES = ["en", "en-US", "en-GB", "pl", "pl-PL"]


class HombeeAssistClient:
    """Keep account credentials out of provider requests and never retry writes."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry

    async def request(self, stage: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Execute one bounded, metered cloud request."""
        data = self.entry.data
        if not data.get("token"):
            raise HomeAssistantError(
                "Associate this Home Assistant in Hombee > Pro configuration."
            )
        url = (
            f"{data['gatewayUrl']}/api/assist/instances/"
            f"{data['instanceId']}/{stage}"
        )
        try:
            async with async_get_clientsession(self.hass).post(
                url,
                json=payload,
                headers={
                    "Authorization": f"Bearer {data['token']}",
                    "Idempotency-Key": str(uuid4()),
                },
                timeout=ClientTimeout(total=40),
                allow_redirects=False,
            ) as response:
                result = await response.json()
                if response.status != 200:
                    raise HomeAssistantError(
                        result.get("message", "Hombee Voice is unavailable.")
                    )
                return result
        except (ClientError, TimeoutError, ValueError) as error:
            raise HomeAssistantError(
                "Cannot reach Hombee Voice. Check the internet connection."
            ) from error


class HombeeAssistEntity(Entity):
    """Shared identity for the three native Assist stages."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, stage: str) -> None:
        self.entry = entry
        self.client: HombeeAssistClient = entry.runtime_data
        self._attr_unique_id = f"hombee_assist_{stage}"
        self._attr_name = f"{ASSIST_NAME} {stage}"


async def async_setup_assist_pipeline(hass: HomeAssistant, entry: ConfigEntry) -> None:
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
