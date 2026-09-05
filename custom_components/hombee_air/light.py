"""Logical light entities managed by Hombee."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_MODE,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_EFFECT,
    ATTR_EFFECT_LIST,
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
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event

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

    @property
    def available(self) -> bool:
        """Mirrors physical-light availability."""
        state = self._source_state
        return state is not None and state.state != STATE_UNAVAILABLE

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
            return {ColorMode.COLOR_TEMP}
        modes: set[ColorMode] = set()
        for value in values:
            try:
                modes.add(ColorMode(value))
            except ValueError:
                continue
        return modes or {ColorMode.COLOR_TEMP}

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
            async_track_state_change_event(
                self.hass,
                self.mapping.physical_entity_id,
                self._source_state_changed,
            )
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turns on the source with color temperature in the first call."""
        service_data = dict(kwargs)
        has_explicit_color = bool(_EXPLICIT_COLOR_KEYS.intersection(service_data))
        if not has_explicit_color and not self.mapping.manual_override:
            service_data[ATTR_COLOR_TEMP_KELVIN] = self.manager.current_kelvin(
                self.mapping
            )
        await self.hass.services.async_call(
            "light",
            "turn_on",
            service_data,
            target={"entity_id": self.mapping.physical_entity_id},
            blocking=True,
            context=self._context,
        )
        if has_explicit_color:
            self.manager.set_manual_override(self.mapping.source_registry_id, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turns off the source and resumes circadian control next time."""
        await self.hass.services.async_call(
            "light",
            "turn_off",
            kwargs,
            target={"entity_id": self.mapping.physical_entity_id},
            blocking=True,
            context=self._context,
        )
        self.manager.set_manual_override(self.mapping.source_registry_id, False)

    async def async_reconcile_temperature(self) -> None:
        """Updates an active source without changing its power state."""
        if not self.is_on or self.mapping.manual_override:
            return
        await self.hass.services.async_call(
            "light",
            "turn_on",
            {
                ATTR_COLOR_TEMP_KELVIN: self.manager.current_kelvin(self.mapping),
                ATTR_TRANSITION: self.mapping.transition_seconds,
            },
            target={"entity_id": self.mapping.physical_entity_id},
            blocking=True,
        )

    @callback
    def _source_state_changed(self, _event: Any) -> None:
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
