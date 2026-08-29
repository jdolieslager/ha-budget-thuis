"""Tests for the redacted config entry diagnostics."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.diagnostics import REDACTED
from homeassistant.const import CONF_TOKEN, CONF_USERNAME

from custom_components.budget_thuis.const import CONF_CONTRACT_ID
from custom_components.budget_thuis.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .conftest import TEST_CONTRACT_ID, TEST_REFRESH_TOKEN, TEST_USERNAME

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry


async def test_diagnostics_redacts_secrets(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Diagnostics never expose the token or username, only availability."""
    diagnostics = await async_get_config_entry_diagnostics(hass, init_integration)

    assert diagnostics["entry_data"] == {
        CONF_USERNAME: REDACTED,
        CONF_CONTRACT_ID: REDACTED,
        CONF_TOKEN: REDACTED,
    }
    serialized = str(diagnostics)
    assert TEST_REFRESH_TOKEN not in serialized
    assert TEST_USERNAME not in serialized
    assert TEST_CONTRACT_ID not in serialized

    assert diagnostics["price_slot_count"] == 8
    assert diagnostics["price_type"] == "gross"
    assert diagnostics["account_available"] == {
        "advance": True,
        "usage": True,
        "free_energy": True,
        "contract": True,
        "mandate": True,
    }
