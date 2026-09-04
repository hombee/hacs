"""Physical light used by the Home Assistant Docker contract."""

from __future__ import annotations

from typing import Any, ClassVar

from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.helpers.device_registry import DeviceInfo

DOMAIN = "hombee_managed_light_test"


async def async_setup_platform(hass, _config, async_add_entities, _discovery=None):
    """Adds one color-temperature light."""
    async_add_entities([ContractPhysicalLight()])


class ContractPhysicalLight(LightEntity):
    """Records every physical turn-on payload."""

    _attr_name = "Contract physical"
    _attr_unique_id = "contract_physical"
    _attr_supported_color_modes: ClassVar[set[ColorMode]] = {ColorMode.COLOR_TEMP}
    _attr_color_mode = ColorMode.COLOR_TEMP
    _attr_min_color_temp_kelvin = 2000
    _attr_max_color_temp_kelvin = 6500
    _attr_is_on = False
    _attr_should_poll = False
    _attr_device_info = DeviceInfo(
        identifiers={(DOMAIN, "contract_physical")},
        name="Contract physical device",
        manufacturer="Hombee test",
        model="CCT",
    )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Records one atomic source call and updates reported state."""
        self.hass.data[DOMAIN]["turn_on_calls"].append(dict(kwargs))
        self._attr_is_on = True
        if "brightness" in kwargs:
            self._attr_brightness = kwargs["brightness"]
        if "color_temp_kelvin" in kwargs:
            self._attr_color_temp_kelvin = kwargs["color_temp_kelvin"]
        self.async_write_ha_state()

    async def async_turn_off(self, **_kwargs: Any) -> None:
        """Turns the physical test light off."""
        self._attr_is_on = False
        self.async_write_ha_state()
