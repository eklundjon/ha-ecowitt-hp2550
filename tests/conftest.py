"""Shared fixtures for ecowitt_hp2550 tests."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Allow custom components to load during tests."""
    yield
