"""Hombee Air: native climate device for the Hombee HVAC unit."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .const import CONF_INSTALLATION_ID, DEFAULT_PORT, DOMAIN, installation_slug
from .coordinator import (
    HombeeAirConfigEntry,
    HombeeAirRuntime,
    create_runtime,
)
from .entity import device_info, is_writable
from .modbus_client import HombeeAirModbusClient, HombeeAirModbusError
from .registers import KIND_COIL, KIND_HOLDING_REGISTER, REGISTERS_BY_KEY

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.CLIMATE,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

SERVICE_WRITE_REGISTER = "write_register"
SERVICE_WRITE_COIL = "write_coil"

_WRITE_REGISTER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_INSTALLATION_ID): cv.string,
        vol.Required("key"): cv.string,
        vol.Required("value"): vol.Coerce(int),
    }
)
_WRITE_COIL_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_INSTALLATION_ID): cv.string,
        vol.Required("key"): cv.string,
        vol.Required("state"): cv.boolean,
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: HombeeAirConfigEntry) -> bool:
    """Sets up one Hombee Air unit from a config entry."""
    slug = installation_slug(entry.data[CONF_INSTALLATION_ID])
    client = HombeeAirModbusClient(
        entry.data[CONF_HOST], entry.data.get(CONF_PORT, DEFAULT_PORT)
    )
    await client.async_connect()
    runtime = create_runtime(hass, entry, slug, client)
    await runtime.fast.async_config_entry_first_refresh()
    await runtime.slow.async_config_entry_first_refresh()
    entry.runtime_data = runtime

    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id, **device_info(slug, entry.title)
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _async_register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HombeeAirConfigEntry) -> bool:
    """Unloads one Hombee Air unit."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.client.async_close()
    return unloaded


def _async_register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_WRITE_REGISTER):
        return

    async def write_register(call: ServiceCall) -> None:
        runtime = _runtime_for_call(hass, call)
        register = _register_for_call(call, KIND_HOLDING_REGISTER)
        try:
            await runtime.async_write(register, call.data["value"])
        except HombeeAirModbusError as error:
            raise HomeAssistantError(str(error)) from error

    async def write_coil(call: ServiceCall) -> None:
        runtime = _runtime_for_call(hass, call)
        register = _register_for_call(call, KIND_COIL)
        try:
            await runtime.async_write(register, int(call.data["state"]))
        except HombeeAirModbusError as error:
            raise HomeAssistantError(str(error)) from error

    hass.services.async_register(
        DOMAIN,
        SERVICE_WRITE_REGISTER,
        write_register,
        schema=_WRITE_REGISTER_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_WRITE_COIL, write_coil, schema=_WRITE_COIL_SCHEMA
    )


def _runtime_for_call(hass: HomeAssistant, call: ServiceCall) -> HombeeAirRuntime:
    slug = installation_slug(call.data[CONF_INSTALLATION_ID])
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.unique_id == slug and isinstance(entry.runtime_data, HombeeAirRuntime):
            return entry.runtime_data
    raise ServiceValidationError(f"No Hombee Air unit with installation id {slug}")


def _register_for_call(call: ServiceCall, kind: str):
    key = call.data["key"]
    register = REGISTERS_BY_KEY.get(key)
    if register is None:
        raise ServiceValidationError(f"Unknown Hombee Air point: {key}")
    if register.kind != kind:
        raise ServiceValidationError(f"Point {key} is a {register.kind}, not a {kind}")
    if not is_writable(register):
        raise ServiceValidationError(f"Point {key} is read-only")
    return register
