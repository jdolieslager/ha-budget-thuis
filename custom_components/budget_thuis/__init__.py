"""The Budget Thuis integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.exceptions import ConfigEntryNotReady

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

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: BudgetThuisConfigEntry) -> bool:
    """Set up Budget Thuis from a config entry."""
    tokens = TokenManager(hass, entry)
    price_coordinator = PriceCoordinator(hass, entry, tokens)
    prices: PriceCoordinator | None = price_coordinator
    account = AccountCoordinator(hass, entry, tokens)

    try:
        await price_coordinator.async_config_entry_first_refresh()
    except ConfigEntryNotReady:
        if not price_coordinator.no_hourly_tariff:
            raise
        # Fixed-price contracts have no hourly tariff; set up without prices.
        _LOGGER.info("No hourly tariff for this contract; price sensors disabled")
        prices = None
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
