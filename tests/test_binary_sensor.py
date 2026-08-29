"""Snapshot and behavior tests for the binary sensor platform."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from aiobudgetthuis import FreeEnergyStatus
from aiobudgetthuis.models import FreeEnergyPeriod
from homeassistant.const import STATE_OFF, STATE_ON, Platform
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    snapshot_platform,
)

from custom_components.budget_thuis.binary_sensor import _free_energy_active
from custom_components.budget_thuis.const import DOMAIN

from .conftest import TEST_ENTRY_ID

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant
    from syrupy.assertion import SnapshotAssertion

NOW = datetime(2026, 1, 15, 13, 0, tzinfo=UTC)


def _period(start_hour: int, end_hour: int) -> FreeEnergyPeriod:
    return FreeEnergyPeriod(
        datetime(2026, 1, 15, start_hour, 0, tzinfo=UTC),
        datetime(2026, 1, 15, end_hour, 0, tzinfo=UTC),
    )


@pytest.mark.freeze_time("2026-01-15 12:00:00+00:00")
@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_binary_sensor_snapshot(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    """Snapshot every binary sensor's registry entry and state."""
    with patch("custom_components.budget_thuis.PLATFORMS", [Platform.BINARY_SENSOR]):
        mock_config_entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    await snapshot_platform(hass, entity_registry, snapshot, mock_config_entry.entry_id)


@pytest.mark.parametrize(
    ("active", "periods", "expected"),
    [
        # No known windows: the API flag is all we have.
        (True, [], True),
        (False, [], False),
        # A window covers now: active regardless of the (stale) flag.
        (False, [_period(12, 14)], True),
        # All windows in the future: fall back to the flag.
        (True, [_period(15, 16)], True),
        (False, [_period(15, 16)], False),
        # A known window passed since the poll and none covers now.
        (True, [_period(10, 11), _period(15, 16)], False),
    ],
)
def test_free_energy_active_derivation(
    active: bool, periods: list[FreeEnergyPeriod], expected: bool
) -> None:
    """Derive "active now" from the window list with the flag as fallback."""
    fe = FreeEnergyStatus(eligible=True, active=active, periods=periods)
    assert _free_energy_active(fe, NOW) is expected


async def test_free_energy_window_switches_without_poll(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The active sensor flips exactly at window boundaries between polls."""
    freezer.move_to("2026-01-15 12:00:00+00:00")
    # One upcoming window: 14:00-15:00 Europe/Amsterdam (13:00-14:00 UTC).
    mock_client.free_energy_status.return_value = FreeEnergyStatus.from_dict(
        {
            "isFreeEnergyEligible": True,
            "isFreeEnergyActive": False,
            "nextFreeEnergyPeriods": [
                {
                    "from": "2026-01-15T14:00:00+01:00",
                    "until": "2026-01-15T15:00:00+01:00",
                }
            ],
        }
    )
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "binary_sensor", DOMAIN, f"{TEST_ENTRY_ID}_free_energy_active"
    )
    assert entity_id is not None
    assert hass.states.get(entity_id).state == STATE_OFF

    # At the window start the scheduled rewrite flips it on: no poll involved.
    freezer.move_to("2026-01-15 13:00:00+00:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get(entity_id).state == STATE_ON
    assert mock_client.free_energy_status.call_count == 1

    # At the window end it flips back off, still without a poll.
    freezer.move_to("2026-01-15 14:00:00+00:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get(entity_id).state == STATE_OFF
    assert mock_client.free_energy_status.call_count == 1
