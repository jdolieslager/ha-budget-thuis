"""Diagnostics with secrets redacted."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_TOKEN, CONF_USERNAME

from .const import CONF_CONTRACT_ID

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .coordinator import BudgetThuisConfigEntry

# Contract id is a customer-account identifier: diagnostics get attached to
# public GitHub issues, so it is redacted like the credentials.
TO_REDACT: Final = {CONF_TOKEN, CONF_PASSWORD, CONF_USERNAME, CONF_CONTRACT_ID}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: BudgetThuisConfigEntry
) -> dict[str, Any]:
    """Return redacted entry data plus data-availability flags."""
    prices = entry.runtime_data.prices.data
    account = entry.runtime_data.account.data
    return {
        "entry_data": async_redact_data(dict(entry.data), TO_REDACT),
        "options": dict(entry.options),
        "price_slot_count": len(prices.slots) if prices else 0,
        "price_type": prices.price_type if prices else None,
        "account_available": {
            "advance": bool(account and account.advance),
            "usage": bool(account and account.usage),
            "free_energy": bool(account and account.free_energy),
            "contract": bool(account and account.contract),
            "mandate": bool(account and account.mandate),
        },
    }
