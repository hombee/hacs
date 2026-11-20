"""Exercise live audio and follow-ups through HA's satellite entry point."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch

from aiohttp import WSMsgType
from homeassistant.components import assist_pipeline, stt, tts
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.core import Context
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hombee.assist_live import LiveTurn
from custom_components.hombee.const import DOMAIN


class VoiceSocket:
    def __init__(self, direct):
        self.direct = direct
        self.frames = asyncio.Queue()
        self.frames.put_nowait({"ready": True})
        self.sent = []
        self.called = False

    async def send_bytes(self, chunk):
        self.sent.append(chunk)
        if not self.called:
            self.called = True
            self.frames.put_nowait(
                {
                    "transcription": {
                        "text": "Turn on the kitchen lamp",
                        "finished": True,
                    }
                }
            )
            self.frames.put_nowait(
                {
                    "call": {
                        "name": "home_command" if self.direct else "home_context",
                        "id": "direct" if self.direct else "context",
                        "args": {},
                    }
                }
            )

    async def send_json(self, frame):
        if frame["id"] == "context":
            tools = json.loads(frame["response"]["page"])["tools"]
            name = next(t["name"] for t in tools if t["name"].endswith("HassTurnOn"))
            self.frames.put_nowait(
                {
                    "call": {
                        "id": "lamp",
                        "name": "call_home_tool",
                        "args": {
                            "name": name,
                            "arguments": json.dumps({"name": "Kitchen lamp"}),
                        },
                    }
                }
            )
            return
        if frame["id"] == "direct":
            assert frame["response"]["matched"]
        else:
            assert frame["id"] == "lamp"
            assert frame["response"]["error"] is False
        self.frames.put_nowait(b"\x10\x01" * 1600)
        self.frames.put_nowait({"spoken": "Kitchen lamp is on."})

    async def receive(self):
        frame = await self.frames.get()
        return SimpleNamespace(
            type=WSMsgType.BINARY if isinstance(frame, bytes) else WSMsgType.TEXT,
            data=frame if isinstance(frame, bytes) else json.dumps(frame),
        )

    async def close(self):
        pass


async def microphone():
    yield b"\x01\x01" * 3200
    await asyncio.Event().wait()


async def silent():
    yield bytes(3200)


async def assert_reply(hass, events):
    assert events["stt-start"]["audio_processing"]["requires_external_vad"] is False
    result = events["intent-end"]["intent_output"]
    assert result["continue_conversation"]
    assert result["response"]["speech"]["plain"]["speech"] == "Kitchen lamp is on."
    stream = tts.async_get_stream(hass, events["run-start"]["tts_output"]["token"])
    wav = b"".join([part async for part in stream.async_stream_result()])
    assert wav.startswith(b"RIFF") and b"\x10\x01" * 100 in wav


async def test_satellite_streams_audio_and_reuses_session(hass, aioclient_mock):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="assist",
        data={
            "entry_kind": "assist",
            "instanceId": "a" * 32,
            "gatewayUrl": "https://hombee.app",
            "token": "test-voice",
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    base = f"https://hombee.app/api/assist/instances/{'a'*32}"
    aioclient_mock.post(base + "/turn", json={"expiresAt": 4070908800000})
    aioclient_mock.post(base + "/finish", json={"finished": True})
    aioclient_mock.delete(base + "/live", status=204)
    hass.states.async_set("light.kitchen", "off", {"friendly_name": "Kitchen lamp"})
    async_expose_entity(hass, "conversation", "light.kitchen", True)
    actions = []

    async def turn_on(call):
        actions.append(call)

    hass.services.async_register("light", "turn_on", turn_on)

    connections = []

    async def connect(_session, url, *, headers, **kwargs):
        assert url == base + "/live"
        socket = VoiceSocket(direct=not connections)
        connections.append((headers, socket))
        return socket

    playback = LiveTurn.playback

    async def observed_playback(live):
        async for part in playback(live):
            if part.startswith(b"\x10\x01"):
                # Consume native samples before the gateway completes its response.
                live.socket.frames.put_nowait({"done": True, "ended": False})
            yield part

    async def run(chat_id, audio):
        events = []

        def receive(event):
            if event.type == "intent-end":
                assert any(e.data and e.data.get("tts_start_streaming") for e in events)
            events.append(event)

        async with asyncio.timeout(8):
            await assist_pipeline.async_pipeline_from_audio_stream(
                hass,
                context=Context(),
                event_callback=receive,
                pipeline_id=entry.options["pipeline_id"],
                conversation_id=chat_id,
                stt_metadata=stt.SpeechMetadata(
                    language="en",
                    format=stt.AudioFormats.WAV,
                    codec=stt.AudioCodecs.PCM,
                    bit_rate=stt.AudioBitRates.BITRATE_16,
                    sample_rate=stt.AudioSampleRates.SAMPLERATE_16000,
                    channel=stt.AudioChannels.CHANNEL_MONO,
                ),
                stt_stream=audio,
                tts_audio_output={"preferred_format": "wav"},
            )
        return {event.type: event.data for event in events}

    with (
        patch("aiohttp.ClientSession.ws_connect", new=connect),
        patch.object(LiveTurn, "playback", observed_playback),
    ):
        first = await run(None, microphone())
        assert "error" not in first, first
        chat_id = first["run-start"]["conversation_id"]
        await assert_reply(hass, first)
        with patch("custom_components.hombee.assist_live.MicroVad") as vad:
            vad.return_value.Process10ms.return_value = 1
            second = await run(chat_id, microphone())
            assert "error" not in second, second
            await assert_reply(hass, second)
        idle = await run(chat_id, silent())
        assert idle["error"]["code"] == "stt-no-text-recognized"
    assert len(actions) == len(connections) == 2
    (first_headers, _), (second_headers, _) = connections
    assert (
        first_headers["X-Hombee-Live-Session"]
        == second_headers["X-Hombee-Live-Session"]
    )
    assert (
        first_headers["X-Hombee-Assist-Turn"] != second_headers["X-Hombee-Assist-Turn"]
    )
    assert [first_headers["X-Hombee-Resume"], second_headers["X-Hombee-Resume"]] == [
        "0",
        "1",
    ]
    assert [call[1].path.rsplit("/", 1)[-1] for call in aioclient_mock.mock_calls] == [
        "turn",
        "finish",
    ] * 2 + ["live"]
