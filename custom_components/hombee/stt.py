"""Native Assist transcription using the metered Hombee gateway."""

from __future__ import annotations

import base64
import io
import wave
from collections.abc import AsyncIterable
from dataclasses import replace

from homeassistant.components import stt
from homeassistant.components.stt.models import DEFAULT_AUDIO_PROCESSING
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.chat_session import current_session

from .assist import ASSIST_LANGUAGES, HombeeAssistEntity
from .assist_client import AssistRequestError
from .assist_device_notices import VoiceNotice, before_action_notice
from .assist_live import start_live
from .assist_live_pipeline import active_output

PARALLEL_UPDATES = 0


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    """Load the speech recognition provider."""
    async_add_entities([HombeeSpeechToText(entry)])


class HombeeSpeechToText(stt.SpeechToTextEntity, HombeeAssistEntity):
    """Accept bounded mono PCM from existing Assist satellites."""

    def __init__(self, entry) -> None:
        super().__init__(entry, "transcription")

    @property
    def supported_languages(self):
        return ASSIST_LANGUAGES

    @property
    def supported_formats(self):
        return [stt.AudioFormats.WAV]

    @property
    def supported_codecs(self):
        return [stt.AudioCodecs.PCM]

    @property
    def supported_bit_rates(self):
        return [stt.AudioBitRates.BITRATE_16]

    @property
    def supported_sample_rates(self):
        return [stt.AudioSampleRates.SAMPLERATE_16000]

    @property
    def supported_channels(self):
        return [stt.AudioChannels.CHANNEL_MONO]

    @property
    def audio_processing(self):
        return replace(
            DEFAULT_AUDIO_PROCESSING,
            requires_external_vad=active_output(self.client) is None,
        )

    async def async_process_audio_stream(
        self, metadata: stt.SpeechMetadata, stream: AsyncIterable[bytes]
    ) -> stt.SpeechResult:
        """Package PCM in a verifiable WAV envelope, without storing recordings."""
        target = self.client.notices.target_for_stt(metadata.language)
        if (session := current_session.get()) and (
            output := active_output(self.client)
        ):
            try:
                text = await start_live(
                    self.client,
                    session,
                    metadata.language,
                    stream,
                    target=target,
                    output=output,
                )
                return stt.SpeechResult(text, stt.SpeechResultState.SUCCESS)
            except (HomeAssistantError, KeyError, TypeError, ValueError) as error:
                code = error.code if isinstance(error, AssistRequestError) else ""
                self.client.notices.play(target, before_action_notice(code))
                return stt.SpeechResult("", stt.SpeechResultState.ERROR)
        pcm = bytearray()
        async for chunk in stream:
            pcm.extend(chunk)
            if len(pcm) > 960_000:
                self.client.notices.play(target, VoiceNotice.RETRY)
                return stt.SpeechResult("", stt.SpeechResultState.ERROR)
        if not pcm:
            return stt.SpeechResult("", stt.SpeechResultState.ERROR)
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16000)
            audio.writeframes(bytes(pcm))
        try:
            result = await self.client.request(
                "transcription",
                {
                    "audio": base64.b64encode(buffer.getvalue()).decode("ascii"),
                    "language": metadata.language,
                },
                notice_target=target,
            )
        except (HomeAssistantError, KeyError, TypeError) as error:
            code = error.code if isinstance(error, AssistRequestError) else ""
            self.client.notices.play(target, before_action_notice(code))
            return stt.SpeechResult("", stt.SpeechResultState.ERROR)
        return stt.SpeechResult(result["text"], stt.SpeechResultState.SUCCESS)
