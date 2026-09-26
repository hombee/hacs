"""Exercise native Assist registration and pipeline lifecycle in Home Assistant."""

import base64
import io
import json
import wave
from unittest.mock import patch

import pytest
from homeassistant.components import conversation, stt
from homeassistant.components.assist_pipeline import pipeline
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import Context
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import instance_id
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hombee.const import DOMAIN
from custom_components.hombee.stt import HombeeSpeechToText
from custom_components.hombee.tts import HombeeTextToSpeech


async def test_assist_pipeline_survives_reload_without_duplicates(hass):
    """All three real providers register and the user's pipeline is retained."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Hombee Voice",
        unique_id="assist",
        data={"entry_kind": "assist"},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    registry = er.async_get(hass)
    for domain, stage in (
        ("conversation", "conversation"),
        ("stt", "transcription"),
        ("tts", "speech"),
    ):
        assert registry.async_get_entity_id(domain, DOMAIN, f"hombee_assist_{stage}")
    pipeline_id = entry.options["pipeline_id"]
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.options["pipeline_id"] == pipeline_id
    assert (
        len([p for p in pipeline.async_get_pipelines(hass) if p.id == pipeline_id]) == 1
    )


@pytest.mark.parametrize("exposed", [True, False])
async def test_conversation_executes_exposed_entity_and_reports_tool_result(
    hass, exposed
):
    """Only the remote model is stubbed; HA resolves and executes the intent."""
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="assist", data={"entry_kind": "assist"}
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    hass.states.async_set("light.kitchen", "off", {"friendly_name": "Kitchen lamp"})
    async_expose_entity(hass, "conversation", "light.kitchen", exposed)
    calls = []

    async def turn_on(call):
        calls.append(call)
        hass.states.async_set("light.kitchen", "on", {"friendly_name": "Kitchen lamp"})

    hass.services.async_register("light", "turn_on", turn_on)

    async def model(stage, payload):
        assert stage == "conversation"
        if payload["messages"][-1]["role"] == "tool":
            if exposed:
                assert "light.kitchen" in payload["messages"][-1]["content"]
            else:
                assert "error" in payload["messages"][-1]["content"]
            return {
                "content": "Kitchen lamp is on." if exposed else "Device unavailable.",
                "tool_calls": None,
            }
        names = [tool["function"]["name"] for tool in payload["tools"]]
        turn_on_tool = next(
            (name for name in names if name.endswith("HassTurnOn")), None
        )
        if exposed:
            assert turn_on_tool, names
        return {
            "content": None,
            "tool_calls": [
                {
                    "id": "call_light",
                    "function": {
                        "name": turn_on_tool or "homeassistant__HassTurnOn",
                        "arguments": json.dumps(
                            {"name": "Kitchen lamp", "domain": ["light"]}
                        ),
                    },
                }
            ],
        }

    agent_id = er.async_get(hass).async_get_entity_id(
        "conversation", DOMAIN, "hombee_assist_conversation"
    )
    with patch.object(entry.runtime_data, "request", side_effect=model):
        result = await conversation.async_converse(
            hass,
            "Turn on the kitchen lamp",
            None,
            Context(),
            language="en",
            agent_id=agent_id,
        )
    assert len(calls) == (1 if exposed else 0)
    assert hass.states.get("light.kitchen").state == ("on" if exposed else "off")
    assert result.response.speech["plain"]["speech"] == (
        "Kitchen lamp is on." if exposed else "Device unavailable."
    )


async def test_pairing_checks_identity_and_never_discloses_token(hass, hass_ws_client):
    assert await async_setup_component(hass, DOMAIN, {})
    client = await hass_ws_client(hass)
    settings = {
        "type": "hombee/assist/configure",
        "instanceId": "wrong",
        "token": "t" * 72,
        "gatewayUrl": "https://hombee.app",
    }
    await client.send_json({"id": 1, **settings})
    assert (await client.receive_json())["error"]["code"] == "wrong_instance"
    await client.send_json(
        {"id": 2, **settings, "instanceId": await instance_id.async_get(hass)}
    )
    result = await client.receive_json()
    assert result["success"], result
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.state is ConfigEntryState.LOADED
    await client.send_json({"id": 3, "type": "hombee/assist/status"})
    status = await client.receive_json()
    assert status["result"]["configured"] is True
    assert "token" not in json.dumps(status)


async def test_native_audio_envelope_and_speech(hass):
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="assist", data={"entry_kind": "assist"}
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    metadata = stt.SpeechMetadata(
        language="pl",
        format=stt.AudioFormats.WAV,
        codec=stt.AudioCodecs.PCM,
        bit_rate=stt.AudioBitRates.BITRATE_16,
        sample_rate=stt.AudioSampleRates.SAMPLERATE_16000,
        channel=stt.AudioChannels.CHANNEL_MONO,
    )

    async def audio():
        yield bytes(32000)

    async def gateway(stage, payload):
        if stage == "transcription":
            with wave.open(io.BytesIO(base64.b64decode(payload["audio"])), "rb") as wav:
                assert wav.getframerate() == 16000
                assert wav.getnframes() == 16000
            return {"text": "Włącz światło"}
        assert payload["text"] == "Światło włączone"
        return {"audio": base64.b64encode(b"RIFF test").decode()}

    with patch.object(entry.runtime_data, "request", side_effect=gateway):
        result = await HombeeSpeechToText(entry).async_process_audio_stream(
            metadata, audio()
        )
        assert result.text == "Włącz światło"
        assert result.result is stt.SpeechResultState.SUCCESS
        assert await HombeeTextToSpeech(entry).async_get_tts_audio(
            "Światło włączone", "pl", {}
        ) == ("wav", b"RIFF test")
