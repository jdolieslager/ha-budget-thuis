"""Constants for the Budget Thuis integration.

The refresh token and username entry keys come from
``homeassistant.const`` (``CONF_TOKEN``, ``CONF_USERNAME``).
"""

from __future__ import annotations

from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "budget_thuis"

# Account/usage data updates at most daily; poll it slowly.
ACCOUNT_UPDATE_MINUTES: Final = 180
CONF_CONTRACT_ID: Final = "contract_id"
CONF_LOGIN_LINK: Final = "login_link"  # Flow-only; never stored.
CONF_PRICE_TYPE: Final = "price_type"  # Options key; one of "gross" or "net".
CONF_UPDATE_INTERVAL: Final = "update_interval"  # Options key; minutes.
DEFAULT_PRICE_TYPE: Final = "gross"
DEFAULT_UPDATE_INTERVAL: Final = 30
MANUFACTURER: Final = "Budget Thuis"
PLATFORMS: Final[list[Platform]] = [Platform.SENSOR, Platform.BINARY_SENSOR]
