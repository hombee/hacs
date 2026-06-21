"""Tests for the Hombee Air config flow."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hombee_air.const import DOMAIN

_USER_INPUT = {
    "host": "192.168.1.50",
    "name": "Hombee Air",
    "installation_id": "Test-Unit",
    "port": 502,
}


async def test_user_flow_creates_entry(hass: HomeAssistant) -> None:
    with (
        patch(
            "custom_components.hombee_air.config_flow._probe",
            return_value=True,
        ),
        patch(
            "custom_components.hombee_air.async_setup_entry",
            return_value=True,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _USER_INPUT
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Hombee Air"
    assert result["result"].unique_id == "test_unit"
    assert result["data"]["host"] == "192.168.1.50"


async def test_user_flow_shows_error_when_unreachable(
    hass: HomeAssistant,
) -> None:
    with patch(
        "custom_components.hombee_air.config_flow._probe",
        return_value=False,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _USER_INPUT
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_duplicate_installation_aborts(hass: HomeAssistant) -> None:
    MockConfigEntry(domain=DOMAIN, unique_id="test_unit", data=_USER_INPUT).add_to_hass(
        hass
    )
    with patch("custom_components.hombee_air.config_flow._probe", return_value=True):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _USER_INPUT
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
