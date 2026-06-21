"""Shared fixtures for Hombee Air integration tests."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Loads custom_components/ for every test."""
