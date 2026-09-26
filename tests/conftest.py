"""Shared fixtures for Hombee Air integration tests."""

from __future__ import annotations

import pytest
from homeassistant.setup import async_setup_component


@pytest.fixture(autouse=True)
async def _enable_custom_integrations(enable_custom_integrations: None, hass) -> None:
    """Loads custom_components/ for every test."""
    assert await async_setup_component(hass, "homeassistant", {})
