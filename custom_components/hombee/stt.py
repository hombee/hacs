"""Native Assist transcription using the metered Hombee gateway."""

from __future__ import annotations

import base64
import io
import wave
from collections.abc import AsyncIterable

from homeassistant.components import stt
from homeassistant.exceptions import HomeAssistantError

from .assist import ASSIST_LANGUAGES, HombeeAssistEntity

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

    async def async_process_audio_stream(
        self, metadata: stt.SpeechMetadata, stream: AsyncIterable[bytes]
    ) -> stt.SpeechResult:
        """Package PCM in a verifiable WAV envelope, without storing recordings."""
        pcm = bytearray()
        async for chunk in stream:
            pcm.extend(chunk)
            if len(pcm) > 960_000:
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
            )
        except HomeAssistantError:
            return stt.SpeechResult("", stt.SpeechResultState.ERROR)
        return stt.SpeechResult(result["text"], stt.SpeechResultState.SUCCESS)
