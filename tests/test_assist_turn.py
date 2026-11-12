"""Exercise native pipeline context, early budget denial and no-cost notices."""

import base64
import io
import wave
from asyncio import Barrier, gather
from dataclasses import replace

import pytest
from homeassistant.components import conversation, stt
from homeassistant.components.assist_pipeline import pipeline
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.core import Context
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.chat_session import ChatSession
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hombee.assist_notices import failure_notice
from custom_components.hombee.const import DOMAIN
from custom_components.hombee.tts import HombeeTextToSpeech


def _add_voice_device(hass, name, notices):
    entry = MockConfigEntry(domain="esphome", data={"device_name": name})
    entry.add_to_hass(hass)
    satellite = (
        er.async_get(hass)
        .async_get_or_create("assist_satellite", "esphome", name, config_entry=entry)
        .entity_id
    )

    async def play(call):
        notices.append((satellite, call.data["notice"]))

    hass.services.async_register(
        "esphome", f"{name.replace('-', '_')}_play_hombee_notice", play
    )
    return satellite


@pytest.mark.parametrize(
    ("status", "code", "notice"),
    [
        (403, "subscription_inactive", "access"),
        (401, "not_associated", "access"),
        (429, "budget_exhausted", "allowance"),
        (429, "too_many_active_turns", "retry"),
        (503, "model_unavailable", "retry"),
    ],
)
async def test_simultaneous_stt_errors_play_localized_notices_on_the_right_device(
    hass, aioclient_mock, status, code, notice
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
    aioclient_mock.post(
        f"https://hombee.app/api/assist/instances/{'a' * 32}/turn",
        status=status,
        json={"code": code},
    )
    selected = pipeline.async_get_pipeline(hass, entry.options["pipeline_id"])
    notices = []
    kitchen = _add_voice_device(hass, "hombee-voice-kitchen", notices)
    study = _add_voice_device(hass, "hombee-voice-study", notices)
    all_listening = Barrier(3)
    # A repeat report of the same state must also identify the attempt.
    hass.states.async_set(study, "listening")

    async def run(satellite, language):
        def receive(event):
            if satellite and event.type == pipeline.PipelineEventType.STT_START:
                hass.states.async_set(satellite, "listening")

        async def audio():
            await all_listening.wait()
            yield bytes(32000)

        await pipeline.PipelineInput(
            run=pipeline.PipelineRun(
                hass=hass,
                context=Context(),
                pipeline=replace(selected, language=language, stt_language=language),
                start_stage=pipeline.PipelineStage.STT,
                end_stage=pipeline.PipelineStage.TTS,
                event_callback=receive,
            ),
            session=ChatSession(conversation_id=satellite or "companion-app"),
            satellite_id=satellite,
            stt_metadata=stt.SpeechMetadata(
                language="",
                format=stt.AudioFormats.WAV,
                codec=stt.AudioCodecs.PCM,
                bit_rate=stt.AudioBitRates.BITRATE_16,
                sample_rate=stt.AudioSampleRates.SAMPLERATE_16000,
                channel=stt.AudioChannels.CHANNEL_MONO,
            ),
            stt_stream=audio(),
        ).execute(validate=True)

    await gather(run(kitchen, "pl-PL"), run(study, "en-GB"), run(None, "en"))
    await hass.async_block_till_done()
    assert sorted(notices) == sorted(
        [(kitchen, f"{notice}_pl"), (study, f"{notice}_en")]
    )
    assert aioclient_mock.call_count == 3


@pytest.mark.parametrize(
    ("budget_available", "speech_available"),
    [(True, True), (False, True), (True, False)],
)
async def test_native_pipeline_reserves_before_local_action_and_passes_turn_to_tts(
    hass, aioclient_mock, budget_available, speech_available
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
    notices = []
    satellite = _add_voice_device(hass, "hombee-voice", notices)
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
        f"{base}/speech",
        status=200 if speech_available else 503,
        json=(
            {"audio": base64.b64encode(buffer.getvalue()).decode()}
            if speech_available
            else {"code": "speech_unavailable"}
        ),
    )
    aioclient_mock.post(f"{base}/finish", json={"finished": True})
    selected = next(
        item
        for item in pipeline.async_get_pipelines(hass)
        if item.id == entry.options["pipeline_id"]
    )
    events = []

    def receive(event):
        events.append(event)
        if event.type == pipeline.PipelineEventType.STT_START:
            hass.states.async_set(satellite, "listening")

    run = pipeline.PipelineRun(
        hass=hass,
        context=Context(),
        pipeline=selected,
        start_stage=pipeline.PipelineStage.STT,
        end_stage=pipeline.PipelineStage.TTS,
        event_callback=receive,
    )

    async def audio():
        yield bytes(32000)

    await pipeline.PipelineInput(
        run=run,
        satellite_id=satellite,
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
        assert notices == ([] if speech_available else [(satellite, "uncertain_en")])
    else:
        assert notices == [(satellite, "allowance_en")]
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


@pytest.mark.parametrize("local_playback", [False, True])
async def test_budget_denial_has_spoken_notice_without_more_requests(
    hass, aioclient_mock, local_playback
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
    notices = []
    satellite = (
        _add_voice_device(hass, "hombee-voice", notices) if local_playback else None
    )
    result = await conversation.async_converse(
        hass,
        "Hello",
        None,
        Context(),
        language="pl",
        agent_id=selected.conversation_engine,
        satellite_id=satellite,
    )
    await hass.async_block_till_done()
    if local_playback:
        assert result.response.speech["plain"]["speech"] == ""
        assert notices == [(satellite, "allowance_pl")]
        assert [
            call[1].path.rsplit("/", 1)[-1] for call in aioclient_mock.mock_calls
        ] == [
            "turn",
            "finish",
        ]
        return
    assert result.response.speech["plain"]["speech"] == failure_notice("pl")
    provider = HombeeTextToSpeech(entry)
    provider.hass = hass
    before = aioclient_mock.call_count
    extension, audio = await provider.async_get_tts_audio(
        failure_notice("pl"), "pl", {}
    )
    assert extension == "wav" and audio[:4] == b"RIFF"
    assert aioclient_mock.call_count == before
