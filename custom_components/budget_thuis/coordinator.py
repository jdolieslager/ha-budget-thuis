"""Coordinators: shared token manager + fast price + slow account data."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
import time
from typing import TYPE_CHECKING, Any, Final, override
from zoneinfo import ZoneInfo

from aiobudgetthuis import (
    BudgetThuisAuthError,
    BudgetThuisClient,
    BudgetThuisConnectionError,
    ContractInfo,
    FreeEnergyStatus,
    Mandate,
    MonthlyAmount,
    PriceData,
    UsageSummary,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_TOKEN
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    ACCOUNT_UPDATE_MINUTES,
    CONF_CONTRACT_ID,
    CONF_PRICE_TYPE,
    CONF_UPDATE_INTERVAL,
    DEFAULT_PRICE_TYPE,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
)

if TYPE_CHECKING:
    from collections.abc import Coroutine

    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)
_TZ: Final = ZoneInfo("Europe/Amsterdam")
# Refresh this many seconds before the reported expiry to absorb clock skew.
_TOKEN_SKEW: Final = 60

type BudgetThuisConfigEntry = ConfigEntry[RuntimeData]


@dataclass(slots=True, kw_only=True)
class RuntimeData:
    """Runtime objects stored on the config entry."""

    prices: PriceCoordinator
    account: AccountCoordinator


class TokenManager:
    """Owns the client + token lifecycle; shared by both coordinators."""

    def __init__(self, hass: HomeAssistant, entry: BudgetThuisConfigEntry) -> None:
        """Initialize the manager with a HA-managed client session."""
        self.hass = hass
        self.entry = entry
        self.client = BudgetThuisClient(async_get_clientsession(hass))
        self._access_token: str | None = None
        self._expires_at: float = 0.0
        self._lock = asyncio.Lock()

    async def async_access_token(self) -> str:
        """Return a valid access token, refreshing it when (nearly) expired.

        Raises:
            ConfigEntryAuthFailed: If the stored refresh token is rejected.
            UpdateFailed: If the token endpoint cannot be reached.
        """
        async with self._lock:
            if self._access_token and time.time() < self._expires_at - _TOKEN_SKEW:
                return self._access_token
            try:
                tokens = await self.client.refresh(self.entry.data[CONF_TOKEN])
            except BudgetThuisAuthError as err:
                raise ConfigEntryAuthFailed("refresh token rejected") from err
            except BudgetThuisConnectionError as err:
                raise UpdateFailed(f"token refresh failed: {err}") from err

            self._access_token = tokens.access_token
            self._expires_at = tokens.expires_at
            if (
                tokens.refresh_token
                and tokens.refresh_token != self.entry.data[CONF_TOKEN]
            ):
                self.hass.config_entries.async_update_entry(
                    self.entry,
                    data={**self.entry.data, CONF_TOKEN: tokens.refresh_token},
                )
            return self._access_token


class PriceCoordinator(DataUpdateCoordinator[PriceData]):
    """Fast coordinator: hourly electricity prices."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: BudgetThuisConfigEntry,
        tokens: TokenManager,
    ) -> None:
        """Initialize with the user-configured polling interval."""
        interval = entry.options.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_prices",
            update_interval=timedelta(minutes=interval),
        )
        self.entry = entry
        self._tokens = tokens

    @override
    async def _async_update_data(self) -> PriceData:
        async with asyncio.timeout(30):
            token = await self._tokens.async_access_token()
            now = datetime.now(_TZ)
            midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
            try:
                details = await self._tokens.client.hourly_tariff(
                    token,
                    self.entry.data[CONF_CONTRACT_ID],
                    midnight,
                    midnight + timedelta(days=2),
                )
            except BudgetThuisAuthError as err:
                raise ConfigEntryAuthFailed(str(err)) from err
            except BudgetThuisConnectionError as err:
                raise UpdateFailed(f"tariff fetch failed: {err}") from err
        price_type = self.entry.options.get(CONF_PRICE_TYPE, DEFAULT_PRICE_TYPE)
        return PriceData(details, price_type)


@dataclass(slots=True, kw_only=True)
class AccountData:
    """Snapshot of the slow-moving account endpoints; None means unavailable."""

    advance: MonthlyAmount | None = None
    usage: UsageSummary | None = None
    free_energy: FreeEnergyStatus | None = None
    contract: ContractInfo | None = None
    mandate: Mandate | None = None
    # First of the month the usage data was fetched for; MTD sensors report it
    # as last_reset so state and reset stay consistent even across rollover.
    month_start: datetime | None = None


def _month_summary(raw: UsageSummary, month_start: datetime) -> UsageSummary:
    """Rebuild the summary bounded to the current month.

    The fetch window starts a few days before the month so "yesterday" exists
    on the 1st. MTD totals must only sum in-month days, and `latest` must be a
    finalized day — never the provisional current day the library falls back to.
    """
    month_days = [d for d in raw.days if d.day >= month_start.date()]
    final_days = [d for d in raw.days if d.is_final]
    return UsageSummary(
        latest=final_days[-1] if final_days else None,
        mtd_consumption=round(sum(d.consumption_kwh for d in month_days), 3),
        mtd_production=round(sum(d.production_kwh for d in month_days), 3),
        mtd_cost=round(sum(d.cost_gross for d in month_days), 2),
        days=month_days,
    )


class AccountCoordinator(DataUpdateCoordinator[AccountData]):
    """Slow coordinator: installment, daily usage/cost, free energy, contract.

    These update at most daily, so it polls infrequently. Every endpoint is
    best-effort so one broken upstream backend can't hold the rest hostage: a
    failed call yields None (its sensor reads unknown) and the update as a
    whole only fails when every endpoint is down. Auth errors always escalate.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: BudgetThuisConfigEntry,
        tokens: TokenManager,
    ) -> None:
        """Initialize with the fixed slow polling interval."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_account",
            update_interval=timedelta(minutes=ACCOUNT_UPDATE_MINUTES),
            always_update=False,
        )
        self.entry = entry
        self._tokens = tokens
        self._degraded: set[str] = set()

    @override
    async def _async_update_data(self) -> AccountData:
        async with asyncio.timeout(60):
            return await self._async_fetch()

    async def _async_fetch(self) -> AccountData:
        token = await self._tokens.async_access_token()
        client = self._tokens.client
        cid = self.entry.data[CONF_CONTRACT_ID]

        now = datetime.now(_TZ)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        # Start a few days early so "yesterday" exists on the 1st of the month;
        # _month_summary rebounds the MTD totals to the month itself.
        usage_from = month_start - timedelta(days=3)
        tomorrow = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(
            days=1
        )

        failed: set[str] = set()

        async def _best_effort[T](coro: Coroutine[Any, Any, T], label: str) -> T | None:
            try:
                return await coro
            except BudgetThuisAuthError as err:
                raise ConfigEntryAuthFailed(str(err)) from err
            except BudgetThuisConnectionError as err:
                _LOGGER.debug("%s fetch failed: %s", label, err)
                failed.add(label)
                return None

        advance, usage, free_energy, contract, mandate = await asyncio.gather(
            _best_effort(client.monthly_amount(token, cid), "installment"),
            _best_effort(
                client.usage_summary(token, cid, usage_from, tomorrow), "usage"
            ),
            _best_effort(client.free_energy_status(token, cid), "free_energy"),
            _best_effort(client.contract_info(token, cid), "contract_info"),
            _best_effort(client.daily_reading_mandate(token, cid), "mandate"),
        )
        results = (advance, usage, free_energy, contract, mandate)
        if failed and all(r is None for r in results):
            raise UpdateFailed(
                f"all account endpoints failed: {', '.join(sorted(failed))}"
            )
        # Log degradation once on change, not every poll.
        if failed != self._degraded:
            if failed:
                _LOGGER.warning(
                    "Account endpoints unavailable, their sensors read unknown: %s",
                    ", ".join(sorted(failed)),
                )
            else:
                _LOGGER.info("All account endpoints recovered")
            self._degraded = failed
        return AccountData(
            advance=advance,
            usage=_month_summary(usage, month_start) if usage else None,
            free_energy=free_energy,
            contract=contract,
            mandate=mandate,
            month_start=month_start,
        )
