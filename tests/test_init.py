"""Tests for integration setup and unload."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aiobudgetthuis import BudgetThuisAuthError, BudgetThuisConnectionError
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import CONF_TOKEN
import pytest

from custom_components.budget_thuis.coordinator import RuntimeData

from .conftest import TEST_REFRESH_TOKEN

if TYPE_CHECKING:
    from unittest.mock import MagicMock

    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry


async def test_setup_and_unload_entry(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """A successful setup populates runtime_data and unloads cleanly."""
    entry = init_integration

    assert entry.state is ConfigEntryState.LOADED
    assert isinstance(entry.runtime_data, RuntimeData)
    assert entry.runtime_data.prices.last_update_success
    assert entry.runtime_data.account.last_update_success
    # The API returned the same refresh token, so entry.data must be untouched.
    assert entry.data[CONF_TOKEN] == TEST_REFRESH_TOKEN

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_options_update_reloads_entry(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Finishing the options flow reloads the entry so new intervals take effect."""
    entry = init_integration
    coordinator_before = entry.runtime_data.prices

    result = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"], {"update_interval": 60, "price_type": "gross"}
    )
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.prices is not coordinator_before


@pytest.mark.parametrize("method", ["refresh", "hourly_tariff"])
async def test_setup_retries_on_connection_error(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    method: str,
) -> None:
    """Unreachable auth or price data (the core) defers setup."""
    getattr(mock_client, method).side_effect = BudgetThuisConnectionError("offline")
    mock_config_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_survives_account_endpoint_outage(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
) -> None:
    """A broken account endpoint degrades its sensor but never blocks setup."""
    mock_client.monthly_amount.side_effect = BudgetThuisConnectionError("upstream 404")
    mock_config_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.LOADED
    account = mock_config_entry.runtime_data.account
    assert account.last_update_success
    assert account.data.advance is None
    assert account.data.usage is not None
    assert mock_config_entry.runtime_data.prices.last_update_success


async def test_setup_auth_failure_starts_single_reauth(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
) -> None:
    """A rejected refresh token fails setup and starts exactly one reauth flow."""
    mock_client.refresh.side_effect = BudgetThuisAuthError("refresh token rejected")
    mock_config_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert len(flows) == 1
    assert flows[0]["context"]["source"] == SOURCE_REAUTH
    assert flows[0]["step_id"] == "reauth_confirm"
