"""Shared validation and serialized configuration writes for UI and API clients."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import replace
from functools import wraps
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.exceptions import HomeAssistantError

from .brightness import PERCENT, finite_number

if TYPE_CHECKING:
    from .managed_lighting import ManagedLightingManager

LIGHT_SETTINGS_SCHEMA = vol.Schema(
    {
        vol.Required("adapt_brightness"): bool,
        vol.Required("adapt_color"): bool,
        vol.Required("min_brightness"): PERCENT,
        vol.Required("max_brightness"): PERCENT,
        vol.Required("transition_seconds"): vol.All(
            finite_number, vol.Range(min=0, max=60)
        ),
    }
)
LIGHT_SETTING_KEYS = tuple(str(key) for key in LIGHT_SETTINGS_SCHEMA.schema)


class LightingConfigurationConflictError(HomeAssistantError):
    """The caller read an older configuration."""


def light_settings(mapping: Any) -> dict[str, Any]:
    """Return only editable settings, without runtime or identity fields."""
    return {key: getattr(mapping, key) for key in LIGHT_SETTING_KEYS}


def validate_light_settings(data: dict[str, Any]) -> dict[str, Any]:
    """Validate the complete result of a settings edit."""
    validated = LIGHT_SETTINGS_SCHEMA(data)
    if validated["min_brightness"] > validated["max_brightness"]:
        raise vol.Invalid("Minimum brightness exceeds maximum")
    return validated


def configuration_state(manager: ManagedLightingManager) -> dict[str, Any]:
    """Configuration only; live sensor reports must not invalidate revisions."""
    return {
        "brightness_enabled": manager.brightness_enabled,
        "color_enabled": manager.enabled,
        "profiles": {key: value.to_dict() for key, value in manager.profiles.items()},
        "activities": dict(manager.activities),
        "lights": {
            mapping.public_entity_id: {
                "settings": light_settings(mapping),
                "area_id": (
                    None
                    if manager.room_key(mapping).startswith("light:")
                    else manager.room_key(mapping)
                ),
            }
            for mapping in manager.mappings.values()
        },
    }


def configuration_revision(manager: ManagedLightingManager) -> str:
    """A stable revision across restarts and equivalent repeated assignments."""
    content = json.dumps(
        _canonical_numbers(configuration_state(manager)),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(content.encode()).hexdigest()


def _canonical_numbers(value: Any) -> Any:
    """JSON distinguishes 50 and 50.0 although settings validation does not."""
    if isinstance(value, dict):
        return {key: _canonical_numbers(item) for key, item in value.items()}
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def configuration_change(method: Callable[..., Awaitable[None]]):
    """Serialize UI and API edits and reject stale API requests before any effects."""

    @wraps(method)
    async def wrapped(
        manager: ManagedLightingManager,
        *args: Any,
        expected_revision: str | None = None,
        **kwargs: Any,
    ) -> None:
        async with manager.configuration_lock:
            if (
                expected_revision is not None
                and expected_revision != configuration_revision(manager)
            ):
                raise LightingConfigurationConflictError(
                    "Lighting settings changed. Read the current configuration "
                    "before editing again."
                )
            before = (
                manager.enabled,
                manager.brightness_enabled,
                dict(manager.profiles),
                dict(manager.activities),
                dict(manager.mappings),
            )
            try:
                await method(manager, *args, **kwargs)
            except OSError:
                manager.enabled, manager.brightness_enabled = before[:2]
                manager.profiles, manager.activities = before[2:4]
                restore_light_settings(manager, before[4])
                manager.lux_controllers.clear()
                raise

    return wrapped


def restore_light_settings(
    manager: ManagedLightingManager, saved: dict[str, Any]
) -> None:
    """Restore settings after a failed save; keep concurrent manual changes."""
    for source_id, old in saved.items():
        current = manager.mappings.get(source_id)
        if current is not None:
            manager._update_mapping(
                replace(
                    current,
                    **light_settings(old),
                    manual_override=old.manual_override or current.manual_override,
                    brightness_override=old.brightness_override
                    or current.brightness_override,
                )
            )
