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


def limit_notice(language: str, completed: int, failed: int, pending: int) -> str:
    """Summarize recorded results when a defensive turn guard stops new actions."""
    if language.startswith("pl"):
        return (
            f"Osiągnięto limit tej tury. Ukończone czynności: {completed}; "
            f"nieudane: {failed}. Nie wykonano {pending} oczekujących czynności. "
            "Poproś ponownie o dokończenie pozostałej pracy."
        )
    return (
        f"This turn reached its limit. Operations completed: {completed}; "
        f"failed: {failed}. I did not run {pending} pending operations. "
        "Ask again for the remaining work."
    )


async def async_notice_audio(hass, message: str):
    """Only exact notice text is eligible, avoiding cached false confirmations."""
    for language, notice in FAILURE_NOTICES.items():
        if message == notice:
            path = Path(__file__).parent / "audio" / f"unavailable_{language}.wav"
            return await hass.async_add_executor_job(path.read_bytes)
    return None
