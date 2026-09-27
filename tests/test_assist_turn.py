"""Exercise native pipeline context, early budget denial and no-cost notices."""

import base64
import io
import wave

import pytest
from homeassistant.components import conversation, stt
from homeassistant.components.assist_pipeline import pipeline
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.core import Context
from homeassistant.helpers.chat_session import ChatSession
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hombee.assist_notices import failure_notice
from custom_components.hombee.const import DOMAIN
from custom_components.hombee.tts import HombeeTextToSpeech


@pytest.mark.parametrize("budget_available", [True, False])
async def test_native_pipeline_reserves_before_local_action_and_passes_turn_to_tts(
    hass, aioclient_mock, budget_available
):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="assist",
        data={
            "entry_kind": "assist",
            "instanceId": "a" * 32,
            "gatewayUrl": "https://hombee.app",
            "token": "secret-test-token",
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    hass.states.async_set("light.kitchen", "off", {"friendly_name": "Kitchen lamp"})
    async_expose_entity(hass, "conversation", "light.kitchen", True)
    actions = []

    async def turn_on(call):
        actions.append(call)

    hass.services.async_register("light", "turn_on", turn_on)
    base = f"https://hombee.app/api/assist/instances/{'a' * 32}"
    aioclient_mock.post(
        f"{base}/turn",
        status=200 if budget_available else 429,
        json=(
            {"expiresAt": 4070908800000}
            if budget_available
            else {
                "code": "budget_exhausted",
                "message": "Monthly allowance reached.",
                "supportId": "11111111-1111-4111-8111-111111111111",
            }
        ),
    )
    aioclient_mock.post(
        f"{base}/transcription", json={"text": "Turn on the kitchen lamp"}
    )
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(bytes(3200))
    aioclient_mock.post(
        f"{base}/speech", json={"audio": base64.b64encode(buffer.getvalue()).decode()}
    )
    aioclient_mock.post(f"{base}/finish", json={"finished": True})
    selected = next(
        item
        for item in pipeline.async_get_pipelines(hass)
        if item.id == entry.options["pipeline_id"]
    )
    events = []
    run = pipeline.PipelineRun(
        hass=hass,
        context=Context(),
        pipeline=selected,
        start_stage=pipeline.PipelineStage.STT,
        end_stage=pipeline.PipelineStage.TTS,
        event_callback=events.append,
    )

    async def audio():
        yield bytes(32000)

    await pipeline.PipelineInput(
        run=run,
        session=ChatSession(conversation_id="turn-test"),
        stt_metadata=stt.SpeechMetadata(
            language="en",
            format=stt.AudioFormats.WAV,
            codec=stt.AudioCodecs.PCM,
            bit_rate=stt.AudioBitRates.BITRATE_16,
            sample_rate=stt.AudioSampleRates.SAMPLERATE_16000,
            channel=stt.AudioChannels.CHANNEL_MONO,
        ),
        stt_stream=audio(),
    ).execute(validate=True)
    await hass.async_block_till_done()
    paths = [call[1].path.rsplit("/", 1)[-1] for call in aioclient_mock.mock_calls]
    assert len(actions) == (1 if budget_available else 0)
    if budget_available:
        assert paths == ["turn", "transcription", "speech", "finish"]
        turn_id = aioclient_mock.mock_calls[0][3]["Idempotency-Key"]
        assert all(
            call[3]["X-Hombee-Assist-Turn"] == turn_id
            for call in aioclient_mock.mock_calls[1:]
        )
    else:
        assert paths == ["turn"]
        diagnostic = entry.runtime_data.diagnostic
        assert diagnostic["code"] == "budget_exhausted"
        assert set(diagnostic) == {
            "code",
            "stage",
            "durationMs",
            "occurredAt",
            "supportId",
        }
        assert any(event.type == pipeline.PipelineEventType.ERROR for event in events)


async def test_budget_denial_has_bundled_spoken_notice_without_more_requests(
    hass, aioclient_mock
):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="assist",
        data={
            "entry_kind": "assist",
            "instanceId": "a" * 32,
            "gatewayUrl": "https://hombee.app",
            "token": "secret-test-token",
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    base = f"https://hombee.app/api/assist/instances/{'a' * 32}"
    aioclient_mock.post(f"{base}/turn", status=429, json={"code": "budget_exhausted"})
    aioclient_mock.post(f"{base}/finish", json={"finished": True})
    selected = next(
        item
        for item in pipeline.async_get_pipelines(hass)
        if item.id == entry.options["pipeline_id"]
    )
    result = await conversation.async_converse(
        hass,
        "Hello",
        None,
        Context(),
        language="pl",
        agent_id=selected.conversation_engine,
    )
    assert result.response.speech["plain"]["speech"] == failure_notice("pl")
    provider = HombeeTextToSpeech(entry)
    provider.hass = hass
    before = aioclient_mock.call_count
    extension, audio = await provider.async_get_tts_audio(
        failure_notice("pl"), "pl", {}
    )
    assert extension == "wav" and audio[:4] == b"RIFF"
    assert aioclient_mock.call_count == before
