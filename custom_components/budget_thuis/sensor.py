"""Sensor platform: price sensors (fast) + account/usage sensors (slow).

Typing notes (validated against HA sensor rules):
- Price-per-kWh sensors: no device_class (there is no per-unit-price class, and
  MONETARY would force state_class=total + a bare-currency unit) + measurement +
  "EUR/kWh". This is what the Energy dashboard's "current price" option consumes.
- Month-to-date energy: device_class ENERGY + state_class TOTAL + monthly
  last_reset (ENERGY cannot be measurement). Month-to-date cost: MONETARY +
  TOTAL + last_reset (MONETARY only permits TOTAL). These are Energy-dashboard
  eligible.
- "Yesterday" values are single completed days (T+1): no state_class, so they
  stay informational rather than publishing misleading statistics.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, Final, override
from zoneinfo import ZoneInfo

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfEnergy
from homeassistant.core import callback
from homeassistant.helpers.event import async_track_time_change
import homeassistant.util.dt as dt_util

from .coordinator import AccountCoordinator, PriceCoordinator
from .entity import BudgetThuisEntity

# Read-only coordinator platform; no serialized device I/O to protect.
PARALLEL_UPDATES = 0

if TYPE_CHECKING:
    from collections.abc import Callable

    from aiobudgetthuis import PriceData, UsageDay
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
    from homeassistant.helpers.typing import StateType

    from .coordinator import AccountData, BudgetThuisConfigEntry

_TZ: Final = ZoneInfo("Europe/Amsterdam")
UNIT_PRICE: Final = "EUR/kWh"
CURRENCY: Final = "EUR"
KWH: Final = UnitOfEnergy.KILO_WATT_HOUR

PRIMARY_KEY: Final = "current_electricity_price"


def _now_local() -> datetime:
    return dt_util.utcnow().astimezone(_TZ)


@dataclass(frozen=True, kw_only=True)
class PriceSensorDescription(SensorEntityDescription):
    """Sensor description with a value extractor for price data."""

    value_fn: Callable[[PriceData, datetime], float | datetime | None]


PRICE_SENSORS: tuple[PriceSensorDescription, ...] = (
    PriceSensorDescription(
        key=PRIMARY_KEY,
        translation_key=PRIMARY_KEY,
        native_unit_of_measurement=UNIT_PRICE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=4,
        value_fn=lambda d, now: s.price if (s := d.current_slot(now)) else None,
    ),
    PriceSensorDescription(
        key="current_price_commodity",
        translation_key="current_price_commodity",
        native_unit_of_measurement=UNIT_PRICE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=4,
        entity_registry_enabled_default=False,
        value_fn=lambda d, now: (
            t.commodity.gross if (t := d.current_tariff(now)) and t.commodity else None
        ),
    ),
    PriceSensorDescription(
        key="current_price_energy_tax",
        translation_key="current_price_energy_tax",
        native_unit_of_measurement=UNIT_PRICE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=4,
        entity_registry_enabled_default=False,
        value_fn=lambda d, now: (
            t.energy_tax.gross
            if (t := d.current_tariff(now)) and t.energy_tax
            else None
        ),
    ),
    PriceSensorDescription(
        key="current_price_surcharge",
        translation_key="current_price_surcharge",
        native_unit_of_measurement=UNIT_PRICE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=4,
        entity_registry_enabled_default=False,
        value_fn=lambda d, now: (
            t.surcharge.gross if (t := d.current_tariff(now)) and t.surcharge else None
        ),
    ),
    PriceSensorDescription(
        key="next_hour_price",
        translation_key="next_hour_price",
        native_unit_of_measurement=UNIT_PRICE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=4,
        value_fn=lambda d, now: s.price if (s := d.next_slot(now)) else None,
    ),
    PriceSensorDescription(
        key="average_price_today",
        translation_key="average_price_today",
        native_unit_of_measurement=UNIT_PRICE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=4,
        value_fn=lambda d, now: d.average_today(now),
    ),
    PriceSensorDescription(
        key="lowest_price_today",
        translation_key="lowest_price_today",
        native_unit_of_measurement=UNIT_PRICE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=4,
        value_fn=lambda d, now: s.price if (s := d.lowest_today(now)) else None,
    ),
    PriceSensorDescription(
        key="highest_price_today",
        translation_key="highest_price_today",
        native_unit_of_measurement=UNIT_PRICE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=4,
        value_fn=lambda d, now: s.price if (s := d.highest_today(now)) else None,
    ),
    PriceSensorDescription(
        key="lowest_price_time_today",
        translation_key="lowest_price_time_today",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda d, now: s.start if (s := d.lowest_today(now)) else None,
    ),
    PriceSensorDescription(
        key="highest_price_time_today",
        translation_key="highest_price_time_today",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda d, now: s.start if (s := d.highest_today(now)) else None,
    ),
    PriceSensorDescription(
        key="percentage_of_max",
        translation_key="percentage_of_max",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda d, now: d.percentage_of_max(now),
    ),
)


@dataclass(frozen=True, kw_only=True)
class AccountSensorDescription(SensorEntityDescription):
    """Sensor description with a value extractor for account data."""

    value_fn: Callable[[AccountData], StateType | datetime]
    # Which best-effort section feeds this sensor; when that endpoint failed
    # (section is None) the entity reports unavailable instead of unknown.
    section_fn: Callable[[AccountData], object | None]
    # Month-to-date totals set last_reset to the first of the current month.
    monthly_reset: bool = False


def _usage(a: AccountData) -> object | None:
    return a.usage


def _free_energy(a: AccountData) -> object | None:
    return a.free_energy


def _contract(a: AccountData) -> object | None:
    return a.contract


def _latest(a: AccountData, getter: Callable[[UsageDay], float]) -> float | None:
    if a.usage and a.usage.latest:
        return getter(a.usage.latest)
    return None


ACCOUNT_SENSORS: tuple[AccountSensorDescription, ...] = (
    AccountSensorDescription(
        key="monthly_advance_amount",
        translation_key="monthly_advance_amount",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY,
        suggested_display_precision=2,
        value_fn=lambda a: a.advance.current_gross if a.advance else None,
        section_fn=lambda a: a.advance,
    ),
    # "Yesterday" = a single completed day (T+1). Informational only: no
    # state_class, so HA doesn't publish misleading mean/min/max statistics for
    # a value that changes once a day. (measurement would be wrong; the ENERGY
    # device class can't combine with measurement, and total_increasing would
    # misread a lower day as a meter reset.)
    AccountSensorDescription(
        key="consumption_yesterday",
        translation_key="consumption_yesterday",
        native_unit_of_measurement=KWH,
        suggested_display_precision=3,
        value_fn=lambda a: _latest(a, lambda day: day.consumption_kwh),
        section_fn=_usage,
    ),
    AccountSensorDescription(
        key="production_yesterday",
        translation_key="production_yesterday",
        native_unit_of_measurement=KWH,
        suggested_display_precision=3,
        value_fn=lambda a: _latest(a, lambda day: day.production_kwh),
        section_fn=_usage,
    ),
    AccountSensorDescription(
        key="net_cost_yesterday",
        translation_key="net_cost_yesterday",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY,
        suggested_display_precision=2,
        value_fn=lambda a: _latest(a, lambda day: day.cost_gross),
        section_fn=_usage,
    ),
    # Month-to-date accumulating totals: ENERGY/MONETARY + total + monthly
    # last_reset. Energy-dashboard eligible and summed correctly.
    AccountSensorDescription(
        key="consumption_month",
        translation_key="consumption_month",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=KWH,
        state_class=SensorStateClass.TOTAL,
        monthly_reset=True,
        suggested_display_precision=1,
        value_fn=lambda a: a.usage.mtd_consumption if a.usage else None,
        section_fn=_usage,
    ),
    AccountSensorDescription(
        key="production_month",
        translation_key="production_month",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=KWH,
        state_class=SensorStateClass.TOTAL,
        monthly_reset=True,
        suggested_display_precision=1,
        value_fn=lambda a: a.usage.mtd_production if a.usage else None,
        section_fn=_usage,
    ),
    AccountSensorDescription(
        key="net_cost_month",
        translation_key="net_cost_month",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY,
        state_class=SensorStateClass.TOTAL,
        monthly_reset=True,
        suggested_display_precision=2,
        value_fn=lambda a: a.usage.mtd_cost if a.usage else None,
        section_fn=_usage,
    ),
    AccountSensorDescription(
        key="next_free_energy_start",
        translation_key="next_free_energy_start",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda a: (
            p.start if a.free_energy and (p := a.free_energy.next_period) else None
        ),
        section_fn=_free_energy,
    ),
    AccountSensorDescription(
        key="next_free_energy_end",
        translation_key="next_free_energy_end",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda a: (
            p.end if a.free_energy and (p := a.free_energy.next_period) else None
        ),
        section_fn=_free_energy,
    ),
    AccountSensorDescription(
        key="standing_charge",
        translation_key="standing_charge",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY,
        suggested_display_precision=2,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda a: a.contract.standing_charge if a.contract else None,
        section_fn=_contract,
    ),
    AccountSensorDescription(
        key="contract_type",
        translation_key="contract_type",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda a: a.contract.contract_type if a.contract else None,
        section_fn=_contract,
    ),
    AccountSensorDescription(
        key="contract_start",
        translation_key="contract_start",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda a: a.contract.start_date if a.contract else None,
        section_fn=_contract,
    ),
    AccountSensorDescription(
        key="meter_reading_mandate",
        translation_key="meter_reading_mandate",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda a: a.mandate.mandate if a.mandate else None,
        section_fn=lambda a: a.mandate,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BudgetThuisConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up price and account sensors from the two coordinators."""
    data = entry.runtime_data
    entities: list[SensorEntity] = [
        PriceSensor(data.prices, desc) for desc in PRICE_SENSORS
    ]
    entities += [AccountSensor(data.account, desc) for desc in ACCOUNT_SENSORS]
    async_add_entities(entities)


class PriceSensor(BudgetThuisEntity[PriceCoordinator], SensorEntity):
    """Hourly-price sensor backed by the fast coordinator."""

    entity_description: PriceSensorDescription
    _unrecorded_attributes = frozenset({"prices", "prices_today", "prices_tomorrow"})

    def __init__(
        self, coordinator: PriceCoordinator, description: PriceSensorDescription
    ) -> None:
        """Initialize the sensor from its description."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{description.key}"

    @override
    async def async_added_to_hass(self) -> None:
        """Re-render on the hour: slot boundaries fall between coordinator polls."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_track_time_change(
                self.hass, self._async_on_the_hour, minute=0, second=0
            )
        )

    @callback
    def _async_on_the_hour(self, _now: datetime) -> None:
        self.async_write_ha_state()

    @property
    @override
    def native_value(self) -> float | datetime | None:
        """Return the described value for the current local time."""
        return self.entity_description.value_fn(self.coordinator.data, _now_local())

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose the full price series on the primary sensor only."""
        if self.entity_description.key != PRIMARY_KEY:
            return None
        data = self.coordinator.data
        now = _now_local()
        today = now.date()
        tomorrow = date.fromordinal(today.toordinal() + 1)
        return {
            "prices_today": data.prices_for(today),
            "prices_tomorrow": data.prices_for(tomorrow),
            "prices": [s.as_attr() for s in data.slots],
            "tomorrow_valid": data.tomorrow_valid(now),
        }


class AccountSensor(BudgetThuisEntity[AccountCoordinator], SensorEntity):
    """Account/usage sensor backed by the slow coordinator."""

    entity_description: AccountSensorDescription

    def __init__(
        self, coordinator: AccountCoordinator, description: AccountSensorDescription
    ) -> None:
        """Initialize the sensor from its description."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{description.key}"

    @property
    @override
    def available(self) -> bool:
        """Unavailable when this sensor's own account endpoint failed."""
        data = self.coordinator.data
        return (
            super().available
            and data is not None
            and self.entity_description.section_fn(data) is not None
        )

    @property
    @override
    def native_value(self) -> StateType | datetime:
        """Return the described value from the account snapshot."""
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    @override
    def last_reset(self) -> datetime | None:
        """Return the month start the MTD data was actually fetched for."""
        if not self.entity_description.monthly_reset:
            return None
        return self.coordinator.data.month_start

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose the installment bounds and free-energy periods."""
        if self.entity_description.key == "monthly_advance_amount":
            adv = self.coordinator.data.advance
            if adv:
                return {"minimum": adv.minimum, "maximum": adv.maximum}
        if self.entity_description.key == "next_free_energy_start":
            fe = self.coordinator.data.free_energy
            if fe:
                return {
                    "periods": [
                        {"start": p.start.isoformat(), "end": p.end.isoformat()}
                        for p in fe.periods
                    ]
                }
        return None
