"""Native Home Assistant forms for room profiles and individual lamp limits."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlowResult, OptionsFlow
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import selector

from .brightness import DEFAULT_PROFILE, BrightnessProfile
from .lighting_configuration import LIGHT_SETTINGS_SCHEMA, validate_light_settings
from .managed_lighting import ManagedLightingManager


class HombeeLightingOptionsFlow(OptionsFlow):
    """Edit stored profiles without reloading or renaming the light entities."""

    _profile_key = DEFAULT_PROFILE
    _source_id: str | None = None

    @property
    def manager(self) -> ManagedLightingManager:
        return self.config_entry.runtime_data

    async def async_step_init(self, user_input=None) -> ConfigFlowResult:
        if not isinstance(self.config_entry.runtime_data, ManagedLightingManager):
            return self.async_abort(reason="lighting_not_loaded")
        return self.async_show_menu(
            step_id="init", menu_options=["default_profile", "room", "light"]
        )

    async def async_step_default_profile(self, user_input=None) -> ConfigFlowResult:
        self._profile_key = DEFAULT_PROFILE
        return await self.async_step_profile()

    async def async_step_room(self, user_input=None) -> ConfigFlowResult:
        errors = {}
        if user_input is not None:
            area_id = user_input["area_id"]
            if ar.async_get(self.hass).async_get_area(area_id) is not None:
                if user_input.get("use_default", False):
                    await self.manager.async_reset_profile(area_id)
                    return self.async_create_entry(
                        title="", data=dict(self.config_entry.options)
                    )
                self._profile_key = area_id
                return await self.async_step_profile()
            errors["base"] = "invalid_area"
        return self.async_show_form(
            step_id="room",
            data_schema=vol.Schema(
                {
                    vol.Required("area_id"): selector.AreaSelector(),
                    vol.Optional("use_default", default=False): bool,
                }
            ),
            errors=errors,
        )

    async def async_step_profile(self, user_input=None) -> ConfigFlowResult:
        errors = {}
        profile = self.manager.profiles.get(
            self._profile_key, self.manager.profiles[DEFAULT_PROFILE]
        )
        if user_input is not None:
            try:
                await self.manager.async_configure_profile(
                    self._profile_key, user_input
                )
            except vol.Invalid:
                errors["base"] = "invalid_profile"
            else:
                return self.async_create_entry(
                    title="", data=dict(self.config_entry.options)
                )
        schema = _profile_form(profile, self._profile_key != DEFAULT_PROFILE)
        return self.async_show_form(
            step_id="profile",
            data_schema=self.add_suggested_values_to_schema(
                schema, user_input or profile.to_dict()
            ),
            errors=errors,
        )

    async def async_step_light(self, user_input=None) -> ConfigFlowResult:
        choices = {
            mapping.source_registry_id: mapping.public_entity_id
            for mapping in self.manager.mappings.values()
        }
        if not choices:
            return self.async_abort(reason="no_lights")
        if user_input is not None:
            self._source_id = user_input["light"]
            return await self.async_step_light_settings()
        return self.async_show_form(
            step_id="light",
            data_schema=vol.Schema({vol.Required("light"): vol.In(choices)}),
        )

    async def async_step_light_settings(self, user_input=None) -> ConfigFlowResult:
        errors = {}
        mapping = self.manager.mappings.get(self._source_id)
        if mapping is None:
            return self.async_abort(reason="no_lights")
        if user_input is not None:
            try:
                data = validate_light_settings(user_input)
                await self.manager.async_configure_light(self._source_id, data)
            except vol.Invalid:
                errors["base"] = "invalid_limits"
            else:
                return self.async_create_entry(
                    title="", data=dict(self.config_entry.options)
                )
        values = {
            str(key): getattr(mapping, str(key)) for key in LIGHT_SETTINGS_SCHEMA.schema
        }
        return self.async_show_form(
            step_id="light_settings",
            data_schema=self.add_suggested_values_to_schema(
                LIGHT_SETTINGS_SCHEMA, user_input or values
            ),
            errors=errors,
        )


def _profile_form(profile: BrightnessProfile, room: bool) -> vol.Schema:
    fields: dict[Any, Any] = {}
    for key in profile.to_dict():
        if key == "illuminance_sensor":
            if room:
                fields[vol.Optional(key)] = selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        filter={"domain": "sensor", "device_class": "illuminance"}
                    )
                )
        elif key.endswith("_time"):
            fields[vol.Required(key)] = selector.TextSelector()
        else:
            maximum = 100 if "brightness" in key else 10000
            if key == "wake_ramp_minutes":
                maximum = 240
            elif key == "wind_down_minutes":
                maximum = 480
            elif key == "sensor_max_age":
                maximum = 3600
            fields[vol.Required(key)] = selector.NumberSelector(
                selector.NumberSelectorConfig(min=1, max=maximum, mode="box", step=1)
            )
    return vol.Schema(fields)
