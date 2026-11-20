"""Native Assist spoken responses through Hombee."""

from __future__ import annotations

import base64

from homeassistant.components.tts import TextToSpeechEntity
from homeassistant.components.tts.entity import TTSAudioResponse
from homeassistant.exceptions import HomeAssistantError

from .assist import ASSIST_LANGUAGES, HombeeAssistEntity
from .assist_device_notices import VoiceNotice
from .assist_notices import async_notice_audio

PARALLEL_UPDATES = 0


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    """Load the speech generation provider."""
    async_add_entities([HombeeTextToSpeech(entry)])


class HombeeTextToSpeech(TextToSpeechEntity, HombeeAssistEntity):
    """AI-generated speech, disclosed during Assist setup."""

    _attr_supported_languages = ASSIST_LANGUAGES
    _attr_default_language = "en-US"

    def __init__(self, entry) -> None:
        super().__init__(entry, "speech")
        # Home Assistant includes default options in its on-disk announcement cache key.
        self._attr_default_options = {
            "hombee_synthesis": "gemini-3.8-flash-lite-tts-achernar"
        }

    async def async_stream_tts_audio(self, request):
        message = "".join([part async for part in request.message_gen])
        if live := self.client.audio_replies.get(message):
            return TTSAudioResponse("wav", live.playback())
        extension, wav = await self.async_get_tts_audio(
            message, request.language, request.options
        )

        async def audio():
            yield wav

        return TTSAudioResponse(extension, audio())

    async def async_get_tts_audio(self, message, language, options):
        """Return an ordinary WAV that all Assist players can consume."""
        if live := self.client.audio_replies.get(message):
            return "wav", b"".join([chunk async for chunk in live.playback()])
        if message.startswith("Hombee voice audio ") and "/" in message:
            raise HomeAssistantError("This voice reply has expired.")
        if notice := await async_notice_audio(self.hass, message):
            await self.client.finish()
            return "wav", notice
        try:
            result = await self.client.request(
                "speech",
                {"text": message, "language": language},
            )
            return "wav", base64.b64decode(result["audio"], validate=True)
        except HomeAssistantError, KeyError, TypeError, ValueError:
            self.client.play_notice(VoiceNotice.UNCERTAIN)
            raise
