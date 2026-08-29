"""Binary sensor platform: free-energy status."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, override

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import callback
from homeassistant.helpers.event import async_track_point_in_time
import homeassistant.util.dt as dt_util

from .coordinator import AccountCoordinator
from .entity import BudgetThuisEntity

# Read-only coordinator platform; no serialized device I/O to protect.
PARALLEL_UPDATES = 0

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from aiobudgetthuis import FreeEnergyStatus
    from homeassistant.core import CALLBACK_TYPE, HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .coordinator import AccountData, BudgetThuisConfigEntry

ACTIVE_KEY = "free_energy_active"


def _free_energy_active(fe: FreeEnergyStatus, now: datetime) -> bool:
    """Derive "active right now" from the known windows.

    The API flag is only as fresh as the last (slow) poll, so a whole window
    can start and end between polls. The window list lets the entity switch
    exactly at boundaries; the flag remains the fallback when the current
    window isn't listed (the API only reports *upcoming* periods).
    """
    if not fe.periods:
        return fe.active
    if any(p.start <= now < p.end for p in fe.periods):
        return True
    if all(p.start > now for p in fe.periods):
        return fe.active
    # A known window has passed since the poll and none covers now.
    return False


@dataclass(frozen=True, kw_only=True)
class FreeEnergyBinaryDescription(BinarySensorEntityDescription):
    """Binary sensor description with a value extractor for account data."""

    value_fn: Callable[[AccountData, datetime], bool | None]


BINARY_SENSORS: tuple[FreeEnergyBinaryDescription, ...] = (
    FreeEnergyBinaryDescription(
        key=ACTIVE_KEY,
        translation_key=ACTIVE_KEY,
        device_class=BinarySensorDeviceClass.RUNNING,
        value_fn=lambda a, now: (
            _free_energy_active(a.free_energy, now) if a.free_energy else None
        ),
    ),
    FreeEnergyBinaryDescription(
        key="free_energy_eligible",
        translation_key="free_energy_eligible",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda a, _now: a.free_energy.eligible if a.free_energy else None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BudgetThuisConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the free-energy binary sensors."""
    coordinator = entry.runtime_data.account
    async_add_entities(
        FreeEnergyBinarySensor(coordinator, desc) for desc in BINARY_SENSORS
    )


class FreeEnergyBinarySensor(BudgetThuisEntity[AccountCoordinator], BinarySensorEntity):
    """Free-energy binary sensor backed by the slow coordinator."""

    entity_description: FreeEnergyBinaryDescription
    _unsub_boundary: CALLBACK_TYPE | None = None

    def __init__(
        self, coordinator: AccountCoordinator, description: FreeEnergyBinaryDescription
    ) -> None:
        """Initialize the binary sensor from its description."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{description.key}"

    @override
    async def async_added_to_hass(self) -> None:
        """Schedule state rewrites at upcoming window boundaries."""
        await super().async_added_to_hass()
        self._schedule_boundary()
        self.async_on_remove(self._cancel_boundary)

    @override
    @callback
    def _handle_coordinator_update(self) -> None:
        self._schedule_boundary()
        super()._handle_coordinator_update()

    @callback
    def _cancel_boundary(self) -> None:
        if self._unsub_boundary is not None:
            self._unsub_boundary()
            self._unsub_boundary = None

    @callback
    def _schedule_boundary(self) -> None:
        """(Re)arm a point-in-time rewrite at the next window start/end."""
        self._cancel_boundary()
        if self.entity_description.key != ACTIVE_KEY:
            return
        data = self.coordinator.data
        fe = data.free_energy if data else None
        if not fe:
            return
        now = dt_util.utcnow()
        upcoming = [t for p in fe.periods for t in (p.start, p.end) if t > now]
        if upcoming:
            self._unsub_boundary = async_track_point_in_time(
                self.hass, self._async_on_boundary, min(upcoming)
            )

    @callback
    def _async_on_boundary(self, _now: datetime) -> None:
        self._unsub_boundary = None
        self.async_write_ha_state()
        self._schedule_boundary()

    @property
    @override
    def available(self) -> bool:
        """Unavailable when the free-energy endpoint failed."""
        data = self.coordinator.data
        return super().available and data is not None and data.free_energy is not None

    @property
    @override
    def is_on(self) -> bool | None:
        """Return the described flag for the current time."""
        return self.entity_description.value_fn(self.coordinator.data, dt_util.utcnow())
