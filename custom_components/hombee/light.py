"""Logical light entities managed by Hombee."""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_MODE,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_EFFECT,
    ATTR_EFFECT_LIST,
    ATTR_FLASH,
    ATTR_HS_COLOR,
    ATTR_MAX_COLOR_TEMP_KELVIN,
    ATTR_MIN_COLOR_TEMP_KELVIN,
    ATTR_RGB_COLOR,
    ATTR_RGBW_COLOR,
    ATTR_RGBWW_COLOR,
    ATTR_SUPPORTED_COLOR_MODES,
    ATTR_TRANSITION,
    ATTR_XY_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
    brightness_supported,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Context, HomeAssistant, callback
from homeassistant.helpers import entity_platform
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util

from .const import LIGHTING_UPDATED

if TYPE_CHECKING:
    from .managed_lighting import ManagedLightingManager, ManagedLightMapping

_EXPLICIT_COLOR_KEYS = {
    "color_temp",
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_EFFECT,
    ATTR_HS_COLOR,
    ATTR_RGB_COLOR,
    ATTR_RGBW_COLOR,
    ATTR_RGBWW_COLOR,
    ATTR_XY_COLOR,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Sets up logical lights for a managed-lighting entry."""
    manager = entry.runtime_data
    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        "resume_adaptation",
        {
            vol.Optional("brightness", default=True): bool,
            vol.Optional("color", default=True): bool,
        },
        "async_resume_adaptation",
    )
    await manager.async_setup_platform(async_add_entities)


class HombeeManagedLight(LightEntity):
    """Public light facade which atomically controls one physical light."""

    _attr_should_poll = False

    def __init__(
        self, manager: ManagedLightingManager, mapping: ManagedLightMapping
    ) -> None:
        self.manager = manager
        self.mapping = mapping
        self._attr_unique_id = mapping.logical_unique_id
        self._attr_name = mapping.name
        self._command_lock = asyncio.Lock()
        self._own_contexts: deque[str] = deque(maxlen=100)
        self._settled_at = dt_util.utcnow()
        self._off_requested = False

    @property
    def available(self) -> bool:
        """Mirrors physical-light availability."""
        state = self._source_state
        return state is not None and state.state not in {
            STATE_UNAVAILABLE,
            STATE_UNKNOWN,
        }

    @property
    def supported_features(self) -> LightEntityFeature:
        """Expose only features the physical light can execute."""
        return LightEntityFeature(self._attribute("supported_features") or 0) & (
            LightEntityFeature.TRANSITION
            | LightEntityFeature.EFFECT
            | LightEntityFeature.FLASH
        )

    @property
    def is_on(self) -> bool:
        """Mirrors physical-light power."""
        state = self._source_state
        return state is not None and state.state == STATE_ON

    @property
    def brightness(self) -> int | None:
        """Mirrors physical-light brightness."""
        return self._attribute(ATTR_BRIGHTNESS)

    @property
    def color_mode(self) -> ColorMode | None:
        """Mirrors the active physical-light color mode."""
        value = self._attribute(ATTR_COLOR_MODE)
        try:
            return ColorMode(value) if value is not None else None
        except ValueError:
            return None

    @property
    def supported_color_modes(self) -> set[ColorMode] | None:
        """Mirrors physical-light color capabilities."""
        values = self._attribute(ATTR_SUPPORTED_COLOR_MODES)
        if not isinstance(values, (list, set, tuple)):
            return {ColorMode.ONOFF}
        modes: set[ColorMode] = set()
        for value in values:
            try:
                modes.add(ColorMode(value))
            except ValueError:
                continue
        return modes or {ColorMode.ONOFF}

    @property
    def automatic_brightness(self) -> bool:
        return (
            self.manager.brightness_enabled
            and self.mapping.adapt_brightness
            and not self.mapping.brightness_override
            and self.effect in {None, "off", "none"}
            and brightness_supported(self.supported_color_modes)
        )

    @property
    def automatic_color(self) -> bool:
        return (
            self.manager.enabled
            and self.mapping.adapt_color
            and not self.mapping.manual_override
            and self.effect in {None, "off", "none"}
            and ColorMode.COLOR_TEMP in self.supported_color_modes
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.manager.brightness_status(self.mapping)

    @property
    def color_temp_kelvin(self) -> int | None:
        """Mirrors physical-light color temperature."""
        return self._attribute(ATTR_COLOR_TEMP_KELVIN)

    @property
    def min_color_temp_kelvin(self) -> int:
        """Mirrors the physical lower color-temperature limit."""
        value = self._attribute(ATTR_MIN_COLOR_TEMP_KELVIN)
        return int(value) if isinstance(value, (int, float)) else 2000

    @property
    def max_color_temp_kelvin(self) -> int:
        """Mirrors the physical upper color-temperature limit."""
        value = self._attribute(ATTR_MAX_COLOR_TEMP_KELVIN)
        return int(value) if isinstance(value, (int, float)) else 6500

    @property
    def hs_color(self) -> tuple[float, float] | None:
        """Mirrors the physical HS color."""
        return self._tuple_attribute(ATTR_HS_COLOR, 2)

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        """Mirrors the physical RGB color."""
        return self._tuple_attribute(ATTR_RGB_COLOR, 3)

    @property
    def rgbw_color(self) -> tuple[int, int, int, int] | None:
        """Mirrors the physical RGBW color."""
        return self._tuple_attribute(ATTR_RGBW_COLOR, 4)

    @property
    def rgbww_color(self) -> tuple[int, int, int, int, int] | None:
        """Mirrors the physical RGBWW color."""
        return self._tuple_attribute(ATTR_RGBWW_COLOR, 5)

    @property
    def xy_color(self) -> tuple[float, float] | None:
        """Mirrors the physical XY color."""
        return self._tuple_attribute(ATTR_XY_COLOR, 2)

    @property
    def effect(self) -> str | None:
        """Mirrors the physical effect."""
        value = self._attribute(ATTR_EFFECT)
        return str(value) if value is not None else None

    @property
    def effect_list(self) -> list[str] | None:
        """Mirrors the physical effect list."""
        value = self._attribute(ATTR_EFFECT_LIST)
        return list(value) if isinstance(value, (list, tuple)) else None

    async def async_added_to_hass(self) -> None:
        """Tracks source state so UI state follows the physical light."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, LIGHTING_UPDATED, self.async_write_ha_state
            )
        )
        self.async_on_remove(
            async_track_state_change_event(
                self.hass,
                self.mapping.physical_entity_id,
                self._source_state_changed,
            )
        )
        if self._source_state is not None and self._source_state.state == STATE_OFF:
            self._clear_overrides()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Apply automatic attributes in the first physical command."""
        async with self._command_lock:
            self._off_requested = False
            service_data = dict(kwargs)
            explicit_color = bool(_EXPLICIT_COLOR_KEYS.intersection(service_data))
            explicit_brightness = ATTR_BRIGHTNESS in service_data
            special = ATTR_FLASH in service_data or ATTR_EFFECT in service_data
            if self.automatic_color and not explicit_color and not special:
                service_data[ATTR_COLOR_TEMP_KELVIN] = self.manager.current_kelvin(
                    self.mapping
                )
            if self.automatic_brightness and not explicit_brightness and not special:
                service_data[ATTR_BRIGHTNESS] = self.manager.brightness_target(
                    self.mapping
                )
            await self._async_send("turn_on", service_data)
            if explicit_color:
                self.manager.set_manual_override(self.mapping.source_registry_id, True)
            if explicit_brightness or ATTR_EFFECT in service_data:
                self.manager.set_brightness_override(
                    self.mapping.source_registry_id, True
                )
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turns off the source and resumes circadian control next time."""
        self._off_requested = True
        async with self._command_lock:
            try:
                await self._async_send("turn_off", kwargs)
            except Exception:
                self._off_requested = False
                raise
            self._clear_overrides()
            self.manager.lux_controllers.pop(self.manager.room_key(self.mapping), None)
            self.async_write_ha_state()

    async def async_reconcile_temperature(self, *, force: bool = False) -> None:
        """Update active lamps, combining changes and skipping redundant commands."""
        async with self._command_lock:
            if (
                not self.available
                or not self.is_on
                or self._off_requested
                or (not force and dt_util.utcnow() < self._settled_at)
            ):
                return
            data: dict[str, Any] = {}
            if self.automatic_color:
                kelvin = self.manager.current_kelvin(self.mapping)
                if (
                    self.color_temp_kelvin is None
                    or abs(self.color_temp_kelvin - kelvin) >= 25
                ):
                    data[ATTR_COLOR_TEMP_KELVIN] = kelvin
            if self.automatic_brightness:
                brightness = self.manager.brightness_target(self.mapping)
                if self.brightness is None or abs(self.brightness - brightness) >= 2:
                    data[ATTR_BRIGHTNESS] = brightness
            if data:
                if self.supported_features & LightEntityFeature.TRANSITION:
                    data[ATTR_TRANSITION] = self.mapping.transition_seconds
                await self._async_send("turn_on", data)
            self.async_write_ha_state()

    async def _async_send(self, service: str, data: dict[str, Any]) -> None:
        """Track our own writes, including reports during hardware transitions."""
        context = Context(parent_id=self._context.id if self._context else None)
        self._own_contexts.append(context.id)
        transition = float(data.get(ATTR_TRANSITION, 0))
        self._settled_at = dt_util.utcnow() + timedelta(seconds=transition + 2)
        await self.hass.services.async_call(
            "light",
            service,
            data,
            target={"entity_id": self.mapping.physical_entity_id},
            blocking=True,
            context=context,
        )
        self.manager.light_changed(self.mapping, transition)

    @callback
    def _clear_overrides(self) -> None:
        self.manager.set_manual_override(self.mapping.source_registry_id, False)
        self.manager.set_brightness_override(self.mapping.source_registry_id, False)

    async def async_resume_adaptation(self, brightness: bool, color: bool) -> None:
        """Resume selected attributes without turning on an inactive lamp."""
        if brightness:
            self.manager.set_brightness_override(self.mapping.source_registry_id, False)
        if color:
            self.manager.set_manual_override(self.mapping.source_registry_id, False)
        await self.async_reconcile_temperature(force=True)
        self.async_write_ha_state()

    @callback
    def _source_state_changed(self, event: Any) -> None:
        old = event.data.get("old_state")
        new = event.data.get("new_state")
        if new is not None and new.state == STATE_OFF:
            self._off_requested = False
            self._clear_overrides()
            self.manager.lux_controllers.pop(self.manager.room_key(self.mapping), None)
        elif (
            new is not None
            and old is not None
            and new.state == old.state == STATE_ON
            and new.context.id not in self._own_contexts
            and (
                new.context.user_id is not None or dt_util.utcnow() >= self._settled_at
            )
        ):
            if new.attributes.get(ATTR_BRIGHTNESS) != old.attributes.get(
                ATTR_BRIGHTNESS
            ):
                self.manager.set_brightness_override(
                    self.mapping.source_registry_id, True
                )
            color_keys = _EXPLICIT_COLOR_KEYS | {ATTR_COLOR_MODE}
            if any(
                new.attributes.get(key) != old.attributes.get(key) for key in color_keys
            ):
                self.manager.set_manual_override(self.mapping.source_registry_id, True)
        self.async_write_ha_state()

    @property
    def _source_state(self):
        return self.hass.states.get(self.mapping.physical_entity_id)

    def _attribute(self, name: str) -> Any:
        state = self._source_state
        return state.attributes.get(name) if state is not None else None

    def _tuple_attribute(self, name: str, length: int):
        value = self._attribute(name)
        if isinstance(value, (list, tuple)) and len(value) == length:
            return tuple(value)
        return None
