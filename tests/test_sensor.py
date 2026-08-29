"""Snapshot and behavior tests for the sensor platform."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from aiobudgetthuis import HourlyTariffDetails, UsageSummary
from homeassistant.const import STATE_UNKNOWN, Platform
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    load_json_object_fixture,
    snapshot_platform,
)

from custom_components.budget_thuis.const import (
    CONF_PRICE_TYPE,
    CONF_UPDATE_INTERVAL,
    DOMAIN,
)
from custom_components.budget_thuis.coordinator import AccountData
from custom_components.budget_thuis.sensor import ACCOUNT_SENSORS, AccountSensor

from .conftest import TEST_ENTRY_ID

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant
    from syrupy.assertion import SnapshotAssertion


def _account_sensor(key: str, data: AccountData) -> AccountSensor:
    """Build a bare AccountSensor around a stub coordinator for unit tests."""
    coordinator = MagicMock()
    coordinator.entry.entry_id = TEST_ENTRY_ID
    coordinator.entry.title = "Budget Thuis Energy"
    coordinator.data = data
    description = next(d for d in ACCOUNT_SENSORS if d.key == key)
    return AccountSensor(coordinator, description)


@pytest.mark.freeze_time("2026-01-15 12:00:00+00:00")
@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_sensor_snapshot(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    """Snapshot every sensor's registry entry and state at a frozen time."""
    with patch("custom_components.budget_thuis.PLATFORMS", [Platform.SENSOR]):
        mock_config_entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    await snapshot_platform(hass, entity_registry, snapshot, mock_config_entry.entry_id)


def _price_entity_id(hass: HomeAssistant) -> str:
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "sensor", DOMAIN, f"{TEST_ENTRY_ID}_current_electricity_price"
    )
    assert entity_id is not None
    return entity_id


async def test_price_rerenders_on_the_hour_without_poll(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The current price switches at the slot boundary between polls."""
    freezer.move_to("2026-01-15 12:00:00+00:00")  # 13:00 Europe/Amsterdam
    mock_config_entry.add_to_hass(hass)
    # Long polling interval so no coordinator update can explain the change.
    hass.config_entries.async_update_entry(
        mock_config_entry,
        options={CONF_UPDATE_INTERVAL: 360, CONF_PRICE_TYPE: "gross"},
    )
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    entity_id = _price_entity_id(hass)
    assert hass.states.get(entity_id).state == "0.25"
    assert mock_client.hourly_tariff.call_count == 1

    freezer.move_to("2026-01-15 13:00:00+00:00")  # 14:00 local: next slot
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert hass.states.get(entity_id).state == "0.31"
    assert mock_client.hourly_tariff.call_count == 1


async def test_tomorrow_prices_not_published(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Late evening without tomorrow's data: unknown prices, tomorrow invalid."""
    freezer.move_to("2026-01-15 22:30:00+00:00")  # 23:30 Europe/Amsterdam
    mock_client.hourly_tariff.return_value = HourlyTariffDetails.from_dict(
        load_json_object_fixture("hourly_tariff_today_only.json")
    )
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    primary = hass.states.get(_price_entity_id(hass))
    assert primary.state == STATE_UNKNOWN  # last slot ended at 16:00
    assert primary.attributes["tomorrow_valid"] is False
    assert primary.attributes["prices_tomorrow"] == []

    registry = er.async_get(hass)
    next_hour = registry.async_get_entity_id(
        "sensor", DOMAIN, f"{TEST_ENTRY_ID}_next_hour_price"
    )
    assert hass.states.get(next_hour).state == STATE_UNKNOWN


def test_yesterday_unknown_without_a_final_day() -> None:
    """No finalized day yet: the yesterday sensors read unknown, not the partial day."""
    sensor = _account_sensor(
        "consumption_yesterday",
        AccountData(usage=UsageSummary(None, 1.2, 0.1, 0.3, days=[])),
    )
    assert sensor.native_value is None


def test_advance_attributes_need_advance_data() -> None:
    """The bounds attributes disappear when the installment endpoint failed."""
    sensor = _account_sensor("monthly_advance_amount", AccountData(advance=None))
    assert sensor.extra_state_attributes is None


def test_free_energy_attributes_need_free_energy_data() -> None:
    """The periods attribute disappears when the free-energy endpoint failed."""
    sensor = _account_sensor("next_free_energy_start", AccountData(free_energy=None))
    assert sensor.extra_state_attributes is None
