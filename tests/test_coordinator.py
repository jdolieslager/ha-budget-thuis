"""Tests for the TokenManager and the two data update coordinators."""

from __future__ import annotations

from datetime import date, datetime, timedelta
import logging
import time
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from aiobudgetthuis import (
    BudgetThuisAuthError,
    BudgetThuisConnectionError,
    Tokens,
    UsageDay,
    UsageSummary,
)
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import CONF_TOKEN, STATE_UNAVAILABLE
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.budget_thuis.const import DOMAIN

from .conftest import TEST_ENTRY_ID, TEST_REFRESH_TOKEN

if TYPE_CHECKING:
    from unittest.mock import MagicMock

    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant
    import pytest

FROZEN_NOW = "2026-01-15 12:00:00+00:00"  # 13:00 Europe/Amsterdam (CET)


def _entity_id(hass: HomeAssistant, key: str, domain: str = "sensor") -> str:
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(domain, DOMAIN, f"{TEST_ENTRY_ID}_{key}")
    assert entity_id is not None
    return entity_id


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


async def _tick(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, minutes: int
) -> None:
    freezer.tick(timedelta(minutes=minutes))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_token_shared_and_reused_until_expiry(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """One refresh serves both coordinators and is reused until near expiry."""
    freezer.move_to(FROZEN_NOW)
    await _setup(hass, mock_config_entry)

    # Both coordinators refreshed during setup, yet only one token request.
    assert mock_client.refresh.call_count == 1
    assert mock_client.login.call_count == 0
    assert mock_client.hourly_tariff.call_count == 1

    # Next price poll (30 min): token still valid, no new refresh.
    await _tick(hass, freezer, 31)
    assert mock_client.hourly_tariff.call_count == 2
    assert mock_client.refresh.call_count == 1

    # Past the 3600 s expiry (minus skew): the next poll refreshes once.
    await _tick(hass, freezer, 31)
    assert mock_client.hourly_tariff.call_count == 3
    assert mock_client.refresh.call_count == 2


async def test_rotated_refresh_token_is_persisted_without_reload(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A new refresh token is stored, but rotation never reloads the entry."""
    freezer.move_to(FROZEN_NOW)
    await _setup(hass, mock_config_entry)
    prices_before = mock_config_entry.runtime_data.prices
    assert mock_config_entry.data[CONF_TOKEN] == TEST_REFRESH_TOKEN

    mock_client.refresh.side_effect = lambda *_a, **_k: Tokens(
        access_token="test-access-token",
        refresh_token="rotated-refresh-token",
        expires_at=time.time() + 3600,
    )
    await _tick(hass, freezer, 31)
    await _tick(hass, freezer, 31)  # past expiry: this poll rotates the token

    assert mock_config_entry.data[CONF_TOKEN] == "rotated-refresh-token"
    # Without an update listener the entry keeps running on the same objects.
    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert mock_config_entry.runtime_data.prices is prices_before


async def test_concurrent_expiry_refreshes_once(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Both coordinators hitting an expired token yields a single refresh."""
    freezer.move_to(FROZEN_NOW)
    await _setup(hass, mock_config_entry)
    assert mock_client.refresh.call_count == 1

    # 181 min later both coordinators poll in the same window (prices 30 min,
    # account 180 min) with the token long expired; the lock must dedupe.
    await _tick(hass, freezer, 181)

    assert mock_client.monthly_amount.call_count == 2
    assert mock_client.hourly_tariff.call_count > 1
    assert mock_client.refresh.call_count == 2


async def test_month_rollover_bounds_mtd_and_yesterday(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """On the 1st, MTD sums only in-month days and yesterday is the last final day."""
    freezer.move_to("2026-02-01 05:00:00+00:00")  # 06:00 Europe/Amsterdam
    mock_client.usage_summary.return_value = UsageSummary.from_days(
        [
            UsageDay(date(2026, 1, 29), 8.0, 0.5, 1.8, is_final=True),
            UsageDay(date(2026, 1, 30), 9.25, 0.6, 2.1, is_final=True),
            UsageDay(date(2026, 1, 31), 10.5, 0.7, 2.4, is_final=True),
            UsageDay(date(2026, 2, 1), 1.2, 0.1, 0.3, is_final=False),
        ]
    )

    await _setup(hass, mock_config_entry)

    # The fetch window starts three days before the month.
    fetch_args = mock_client.usage_summary.await_args.args
    assert fetch_args[2] == datetime(2026, 1, 29, tzinfo=ZoneInfo("Europe/Amsterdam"))
    assert fetch_args[3] == datetime(2026, 2, 2, tzinfo=ZoneInfo("Europe/Amsterdam"))

    # Yesterday = Jan 31 (last finalized day), never the provisional Feb 1.
    yesterday = hass.states.get(_entity_id(hass, "consumption_yesterday"))
    assert yesterday.state == "10.5"

    # MTD sums only February days and resets at the February month start.
    month = hass.states.get(_entity_id(hass, "consumption_month"))
    assert month.state == "1.2"
    assert month.attributes["last_reset"] == "2026-02-01T00:00:00+01:00"
    cost = hass.states.get(_entity_id(hass, "net_cost_month"))
    assert cost.state == "0.3"


async def test_degradation_chain_logs_once_and_recovers(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Partial outage warns once, stays silent while unchanged, then recovers."""
    freezer.move_to(FROZEN_NOW)
    await _setup(hass, mock_config_entry)
    caplog.clear()

    def _warnings() -> list[logging.LogRecord]:
        return [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING and "installment" in r.getMessage()
        ]

    # Partial outage: one warning naming the failed endpoint.
    mock_client.monthly_amount.side_effect = BudgetThuisConnectionError("boom")
    await _tick(hass, freezer, 181)
    assert len(_warnings()) == 1

    # Same outage on the next poll: no repeated warning.
    await _tick(hass, freezer, 181)
    assert len(_warnings()) == 1

    # Total outage: the whole update fails.
    outage_methods = (
        "monthly_amount",
        "usage_summary",
        "free_energy_status",
        "contract_info",
        "daily_reading_mandate",
    )
    for method in outage_methods:
        getattr(mock_client, method).side_effect = BudgetThuisConnectionError("boom")
    await _tick(hass, freezer, 181)
    assert not mock_config_entry.runtime_data.account.last_update_success

    # Recovery: one info log and the sensors come back.
    for method in outage_methods:
        getattr(mock_client, method).side_effect = None
    await _tick(hass, freezer, 181)
    assert mock_config_entry.runtime_data.account.last_update_success
    recovered = [
        r
        for r in caplog.records
        if r.levelno == logging.INFO
        and r.getMessage() == "All account endpoints recovered"
    ]
    assert len(recovered) == 1
    assert hass.states.get(_entity_id(hass, "monthly_advance_amount")).state == "150.0"


async def test_price_update_failure_and_recovery(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A failed price poll marks entities unavailable; the next poll recovers."""
    freezer.move_to(FROZEN_NOW)
    await _setup(hass, mock_config_entry)
    entity_id = _entity_id(hass, "current_electricity_price")
    assert hass.states.get(entity_id).state == "0.25"

    mock_client.hourly_tariff.side_effect = BudgetThuisConnectionError("boom")
    await _tick(hass, freezer, 31)
    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE

    mock_client.hourly_tariff.side_effect = None
    await _tick(hass, freezer, 31)
    # 62 minutes after 13:00 local: the 14:00-15:00 slot.
    assert hass.states.get(entity_id).state == "0.31"


async def test_runtime_auth_failure_starts_single_reauth(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Auth failures in both coordinators result in exactly one reauth flow."""
    freezer.move_to(FROZEN_NOW)
    await _setup(hass, mock_config_entry)

    mock_client.hourly_tariff.side_effect = BudgetThuisAuthError("expired")
    mock_client.monthly_amount.side_effect = BudgetThuisAuthError("expired")
    # Past both intervals (prices 30 min, account 180 min) in one window.
    await _tick(hass, freezer, 181)

    flows = hass.config_entries.flow.async_progress()
    assert len(flows) == 1
    assert flows[0]["context"]["source"] == SOURCE_REAUTH


async def test_account_best_effort_keeps_other_data(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
) -> None:
    """One flaky optional endpoint doesn't blank the rest of the account data."""
    mock_client.usage_summary.side_effect = BudgetThuisConnectionError("flaky")

    await _setup(hass, mock_config_entry)
    account = mock_config_entry.runtime_data.account

    assert account.last_update_success
    assert account.data.usage is None
    assert account.data.advance is not None
    assert account.data.free_energy is not None
    assert account.data.contract is not None
    assert account.data.mandate is not None

    # Usage-backed sensor is unavailable, but the advance sensor keeps its value.
    assert (
        hass.states.get(_entity_id(hass, "consumption_month")).state
        == STATE_UNAVAILABLE
    )
    assert hass.states.get(_entity_id(hass, "monthly_advance_amount")).state == "150.0"


async def test_account_best_effort_auth_error_escalates(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
) -> None:
    """An auth error on an account endpoint still triggers reauth.

    Setup itself survives (prices are fine and share the same token), but the
    account coordinator fails its refresh and starts the reauth flow.
    """
    mock_client.usage_summary.side_effect = BudgetThuisAuthError("expired")
    mock_config_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert not mock_config_entry.runtime_data.account.last_update_success
    flows = hass.config_entries.flow.async_progress()
    assert len(flows) == 1
    assert flows[0]["context"]["source"] == SOURCE_REAUTH


async def test_account_partial_outage_degrades_single_sensor(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A broken installment backend degrades one sensor; the rest keep flowing."""
    freezer.move_to(FROZEN_NOW)
    await _setup(hass, mock_config_entry)

    mock_client.monthly_amount.side_effect = BudgetThuisConnectionError("boom")
    await _tick(hass, freezer, 181)

    account = mock_config_entry.runtime_data.account
    assert account.last_update_success
    assert account.data.advance is None
    assert account.data.usage is not None
    advance = hass.states.get(_entity_id(hass, "monthly_advance_amount"))
    assert advance.state == STATE_UNAVAILABLE
    price = hass.states.get(_entity_id(hass, "current_electricity_price"))
    assert price.state != STATE_UNAVAILABLE

    # Upstream recovers: the sensor comes back on the next poll.
    mock_client.monthly_amount.side_effect = None
    await _tick(hass, freezer, 181)
    advance = hass.states.get(_entity_id(hass, "monthly_advance_amount"))
    assert advance.state == "150.0"


async def test_account_total_outage_marks_entities_unavailable(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Every account endpoint down fails the update; prices are untouched."""
    freezer.move_to(FROZEN_NOW)
    await _setup(hass, mock_config_entry)

    for method in (
        "monthly_amount",
        "usage_summary",
        "free_energy_status",
        "contract_info",
        "daily_reading_mandate",
    ):
        getattr(mock_client, method).side_effect = BudgetThuisConnectionError("boom")
    await _tick(hass, freezer, 181)

    assert not mock_config_entry.runtime_data.account.last_update_success
    advance = hass.states.get(_entity_id(hass, "monthly_advance_amount"))
    assert advance.state == STATE_UNAVAILABLE
    price = hass.states.get(_entity_id(hass, "current_electricity_price"))
    assert price.state != STATE_UNAVAILABLE
