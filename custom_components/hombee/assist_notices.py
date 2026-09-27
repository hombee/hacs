"""Bundled AI-generated notices never need a metered request."""

from pathlib import Path

FAILURE_NOTICES = {
    "en": (
        "Hombee Voice could not finish this request. An action may already have "
        "completed. Check Pro configuration in Hombee for details."
    ),
    "pl": (
        "Hombee Voice nie może dokończyć tego polecenia. Czynność mogła już "
        "zostać wykonana. Sprawdź szczegóły w konfiguracji Pro w aplikacji Hombee."
    ),
}


def failure_notice(language: str) -> str:
    """Return the message whose matching audio ships with this integration."""
    return FAILURE_NOTICES["pl" if language.startswith("pl") else "en"]


async def async_notice_audio(hass, message: str):
    """Only exact notice text is eligible, avoiding cached false confirmations."""
    for language, notice in FAILURE_NOTICES.items():
        if message == notice:
            path = Path(__file__).parent / "audio" / f"unavailable_{language}.wav"
            return await hass.async_add_executor_job(path.read_bytes)
    return None
