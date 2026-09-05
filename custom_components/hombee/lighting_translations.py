"""English and Polish labels used to generate native lighting translations."""

PROFILE_LABELS = {
    "wake_time": ("Wake time, HH:MM", "Godzina pobudki, HH:MM"),
    "sleep_time": ("Sleep time, HH:MM", "Godzina snu, HH:MM"),
    "wake_ramp_minutes": (
        "Morning transition, minutes",
        "Poranne rozjaśnianie, minuty",
    ),
    "wind_down_minutes": (
        "Evening transition, minutes",
        "Wieczorne przyciemnianie, minuty",
    ),
    "day_brightness": ("Day brightness, %", "Jasność w dzień, %"),
    "evening_brightness": ("Relax brightness, %", "Jasność podczas odpoczynku, %"),
    "night_brightness": ("Night brightness, %", "Jasność w nocy, %"),
    "task_brightness": (
        "Reading and cooking brightness, %",
        "Jasność do czytania i gotowania, %",
    ),
    "day_lux": ("Day illuminance, lx", "Natężenie oświetlenia w dzień, lx"),
    "evening_lux": (
        "Relax illuminance, lx",
        "Natężenie oświetlenia podczas odpoczynku, lx",
    ),
    "night_lux": ("Night illuminance, lx", "Natężenie oświetlenia w nocy, lx"),
    "task_lux": (
        "Reading and cooking illuminance, lx",
        "Natężenie do czytania i gotowania, lx",
    ),
    "illuminance_sensor": (
        "Room illuminance sensor",
        "Czujnik natężenia oświetlenia w pomieszczeniu",
    ),
    "sensor_max_age": (
        "Maximum sensor age, seconds",
        "Maksymalny wiek pomiaru, sekundy",
    ),
}
LIGHT_LABELS = {
    "adapt_brightness": ("Automatic brightness", "Automatyczna jasność"),
    "adapt_color": ("Automatic color temperature", "Automatyczna temperatura barwowa"),
    "min_brightness": (
        "Minimum automatic brightness, %",
        "Minimalna automatyczna jasność, %",
    ),
    "max_brightness": (
        "Maximum automatic brightness, %",
        "Maksymalna automatyczna jasność, %",
    ),
    "transition_seconds": ("Transition, seconds", "Czas przejścia, sekundy"),
}
ACTIVITY_LABELS = {
    "inherit": ("Home default", "Domyślny tryb domu"),
    "auto": ("Daily schedule", "Profil dobowy"),
    "reading": ("Reading", "Czytanie"),
    "cooking": ("Cooking", "Gotowanie"),
    "relax": ("Relax", "Odpoczynek"),
    "night": ("Night", "Noc"),
}


def lighting_translations(language: str) -> dict:
    """Return native options, entities, and action labels for one language."""
    index = int(language == "pl")

    def label(english: str, polish: str) -> str:
        return (english, polish)[index]

    return {
        "entity": {
            "switch": {
                "circadian_lighting": {
                    "name": label("Circadian lighting", "Oświetlenie dobowe")
                },
                "adaptive_brightness": {
                    "name": label("Adaptive brightness", "Automatyczna jasność")
                },
            },
            "select": {
                "lighting_activity": {
                    "name": label(
                        "{room} lighting activity", "{room}, tryb oświetlenia"
                    ),
                    "state": {
                        key: value[index] for key, value in ACTIVITY_LABELS.items()
                    },
                }
            },
        },
        "options": {
            "step": {
                "init": {
                    "title": label("Lighting settings", "Ustawienia oświetlenia"),
                    "menu_options": {
                        "default_profile": label(
                            "Default daily profile", "Domyślny profil dobowy"
                        ),
                        "room": label(
                            "Room profile and sensor", "Profil i czujnik pomieszczenia"
                        ),
                        "light": label(
                            "Individual lamp settings", "Ustawienia pojedynczej lampy"
                        ),
                    },
                },
                "room": {
                    "title": label("Choose a room", "Wybierz pomieszczenie"),
                    "data": {
                        "area_id": label("Room", "Pomieszczenie"),
                        "use_default": label(
                            "Restore the default profile", "Przywróć profil domyślny"
                        ),
                    },
                },
                "profile": {
                    "title": label("Brightness profile", "Profil jasności"),
                    "description": label(
                        "Times use Home Assistant's time zone. Brightness is "
                        "used without a fresh lux reading. A room sensor "
                        "measures the combined daylight and lamp light. Clear "
                        "the sensor to use the daily schedule. The default "
                        "profile cannot use a sensor.",
                        "Godziny odnoszą się do strefy czasowej Home "
                        "Assistanta. Jasność procentowa obowiązuje bez "
                        "świeżego pomiaru luksów. Czujnik w pomieszczeniu "
                        "mierzy łącznie światło dzienne i światło lamp. Usuń "
                        "czujnik z formularza, aby używać profilu dobowego. "
                        "Profil domyślny nie korzysta z czujnika.",
                    ),
                    "data": {
                        key: value[index] for key, value in PROFILE_LABELS.items()
                    },
                },
                "light": {
                    "title": label("Choose a lamp", "Wybierz lampę"),
                    "data": {"light": label("Lamp", "Lampa")},
                },
                "light_settings": {
                    "title": label("Lamp settings", "Ustawienia lampy"),
                    "description": label(
                        "Limits apply to automatic changes. Explicit "
                        "brightness and scene settings take priority. Color "
                        "adaptation requires a lamp with color temperature "
                        "support.",
                        "Limity dotyczą zmian automatycznych. Ręcznie wybrana "
                        "jasność i ustawienia scen mają pierwszeństwo. "
                        "Regulacja barwy wymaga lampy obsługującej "
                        "temperaturę barwową.",
                    ),
                    "data": {key: value[index] for key, value in LIGHT_LABELS.items()},
                },
            },
            "error": {
                "invalid_profile": label(
                    "Check the values and HH:MM times. Morning and evening "
                    "transitions must fit between wake and sleep.",
                    "Sprawdź wartości i godziny HH:MM. Poranne i wieczorne "
                    "przejścia muszą zmieścić się między pobudką a snem.",
                ),
                "invalid_limits": label(
                    "Use brightness limits from 1 to 100 with minimum no "
                    "greater than maximum.",
                    "Podaj limity jasności od 1 do 100. Minimum nie może "
                    "przekraczać maksimum.",
                ),
                "invalid_area": label(
                    "This room no longer exists.", "To pomieszczenie już nie istnieje."
                ),
            },
            "abort": {
                "lighting_not_loaded": label(
                    "Load Hombee managed lighting before editing its settings.",
                    "Uruchom zarządzane oświetlenie Hombee przed zmianą ustawień.",
                ),
                "no_lights": label(
                    "No managed lamps are available.",
                    "Brak dostępnych lamp zarządzanych przez Hombee.",
                ),
            },
        },
        "services": {
            "resume_adaptation": {
                "name": label(
                    "Resume automatic lighting", "Przywróć automatyczne oświetlenie"
                ),
                "description": label(
                    "Clears manual overrides for the selected attributes. "
                    "Keeps inactive lamps off.",
                    "Usuwa ręczne ustawienia wybranych parametrów. Pozostawia "
                    "wyłączone lampy wyłączone.",
                ),
                "fields": {
                    "brightness": {
                        "name": label("Brightness", "Jasność"),
                        "description": label(
                            "Resume automatic brightness.",
                            "Przywróć automatyczną jasność.",
                        ),
                    },
                    "color": {
                        "name": label("Color temperature", "Temperatura barwowa"),
                        "description": label(
                            "Resume automatic color temperature.",
                            "Przywróć automatyczną temperaturę barwową.",
                        ),
                    },
                },
            }
        },
    }
