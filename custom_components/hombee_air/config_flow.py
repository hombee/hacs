"""Config flow for the Hombee Air integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT

from .const import CONF_INSTALLATION_ID, DEFAULT_PORT, DOMAIN, installation_slug
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


class HombeeAirConfigFlow(ConfigFlow, domain=DOMAIN):
    """Collects connection details and probes the unit."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handles the user setup step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            slug = installation_slug(user_input[CONF_INSTALLATION_ID])
            await self.async_set_unique_id(slug)
            self._abort_if_unique_id_configured()
            if await _probe(user_input[CONF_HOST], user_input[CONF_PORT]):
                return self.async_create_entry(
                    title=user_input[CONF_NAME], data=user_input
                )
            errors["base"] = "cannot_connect"
        return self.async_show_form(
            step_id="user", data_schema=_USER_SCHEMA, errors=errors
        )


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
