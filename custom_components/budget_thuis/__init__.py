"""The Budget Thuis integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .const import PLATFORMS
from .coordinator import (
    AccountCoordinator,
    BudgetThuisConfigEntry,
    PriceCoordinator,
    RuntimeData,
    TokenManager,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


async def async_setup_entry(hass: HomeAssistant, entry: BudgetThuisConfigEntry) -> bool:
    """Set up Budget Thuis from a config entry."""
    tokens = TokenManager(hass, entry)
    prices = PriceCoordinator(hass, entry, tokens)
    account = AccountCoordinator(hass, entry, tokens)

    await prices.async_config_entry_first_refresh()
    # Account data is nice-to-have: fetch it, but never block setup on it. If
    # the refresh failed, its entities stay unavailable until the next poll.
    await account.async_refresh()

    entry.runtime_data = RuntimeData(prices=prices, account=account)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # No update listener: TokenManager rewrites entry.data on refresh-token
    # rotation, and a listener would reload the whole entry on every rotation.
    # Options changes reload via OptionsFlowWithReload instead.
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: BudgetThuisConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
