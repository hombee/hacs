"""Config flow for the Hombee integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT
from homeassistant.core import callback

from .assist import ASSIST_ENTRY_KIND, ASSIST_NAME
from .const import (
    CONF_ENTRY_KIND,
    CONF_INSTALLATION_ID,
    DEFAULT_PORT,
    DOMAIN,
    ENTRY_KIND_AIR,
    ENTRY_KIND_MANAGED_LIGHTING,
    MANAGED_LIGHTING_UNIQUE_ID,
    installation_slug,
)
from .lighting_options import HombeeLightingOptionsFlow
from .modbus_client import (
    HombeeAirModbusClient,
    HombeeAirModbusError,
    build_read_plan,
)
from .registers import REGISTERS_BY_KEY

_PROBE_KEY = "unit_status"

_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_NAME, default="Hombee Air"): str,
        vol.Required(CONF_INSTALLATION_ID): str,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): vol.Coerce(int),
    }
)


class HombeeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Configures Hombee Air or managed lighting."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        if config_entry.data.get(CONF_ENTRY_KIND) == ASSIST_ENTRY_KIND:
            return HombeeAssistOptionsFlow()
        return HombeeLightingOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Lets the user select the Hombee feature to configure."""
        return self.async_show_menu(
            step_id="user",
            menu_options=[
                ENTRY_KIND_AIR,
                ENTRY_KIND_MANAGED_LIGHTING,
                ASSIST_ENTRY_KIND,
            ],
        )

    async def async_step_assist(self, user_input=None) -> ConfigFlowResult:
        """Make cloud association available without owning Hombee hardware."""
        await self.async_set_unique_id(ASSIST_ENTRY_KIND)
        self._abort_if_unique_id_configured()
        if user_input is not None:
            return self.async_create_entry(
                title=ASSIST_NAME, data={CONF_ENTRY_KIND: ASSIST_ENTRY_KIND}
            )
        return self.async_show_form(
            step_id=ASSIST_ENTRY_KIND, data_schema=vol.Schema({})
        )

    async def async_step_import(self, user_input) -> ConfigFlowResult:
        """Accept a grant delivered by the authenticated administrator API."""
        await self.async_set_unique_id(ASSIST_ENTRY_KIND)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=ASSIST_NAME, data={**user_input, CONF_ENTRY_KIND: ASSIST_ENTRY_KIND}
        )

    async def async_step_air(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collects connection details and probes the air unit."""
        errors: dict[str, str] = {}
        if user_input is not None:
            slug = installation_slug(user_input[CONF_INSTALLATION_ID])
            await self.async_set_unique_id(slug)
            self._abort_if_unique_id_configured()
            if await _probe(user_input[CONF_HOST], user_input[CONF_PORT]):
                return self.async_create_entry(
                    title=user_input[CONF_NAME],
                    data={**user_input, CONF_ENTRY_KIND: ENTRY_KIND_AIR},
                )
            errors["base"] = "cannot_connect"
        return self.async_show_form(
            step_id="air", data_schema=_USER_SCHEMA, errors=errors
        )

    async def async_step_managed_lighting(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Creates the singleton managed-lighting entry."""
        await self.async_set_unique_id(MANAGED_LIGHTING_UNIQUE_ID)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title="Hombee managed lighting",
            data={CONF_ENTRY_KIND: ENTRY_KIND_MANAGED_LIGHTING},
        )


class HombeeAssistOptionsFlow(OptionsFlow):
    """Cloud association is owned by Hombee Pro configuration."""

    async def async_step_init(self, user_input=None) -> ConfigFlowResult:
        return self.async_abort(reason="manage_in_hombee")


async def _probe(host: str, port: int) -> bool:
    """Verifies the unit responds by reading its status register."""
    client = HombeeAirModbusClient(host, port)
    try:
        await client.async_connect()
        await client.async_read(build_read_plan([REGISTERS_BY_KEY[_PROBE_KEY]]))
    except HombeeAirModbusError:
        return False
    finally:
        await client.async_close()
    return True
