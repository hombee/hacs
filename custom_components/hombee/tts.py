"""Native Assist spoken responses through Hombee."""

from __future__ import annotations

import base64

from homeassistant.components.tts import TextToSpeechEntity

from .assist import ASSIST_LANGUAGES, HombeeAssistEntity
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

    async def async_get_tts_audio(self, message, language, options):
        """Return an ordinary WAV that all Assist players can consume."""
        if notice := await async_notice_audio(self.hass, message):
            await self.client.finish()
            return "wav", notice
        result = await self.client.request(
            "speech",
            {
                "text": message,
                "language": language,
            },
        )
        return "wav", base64.b64decode(result["audio"], validate=True)
