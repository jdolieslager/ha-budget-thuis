"""Shared base entity."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import AccountCoordinator, PriceCoordinator


class BudgetThuisEntity[CoordinatorT: PriceCoordinator | AccountCoordinator](
    CoordinatorEntity[CoordinatorT]
):
    """Base entity: one device per config entry (shared by both coordinators)."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: CoordinatorT) -> None:
        """Initialize the entity and its per-entry device."""
        super().__init__(coordinator)
        entry = coordinator.entry
        # No contract id or address in the registry: device data shows up in
        # screenshots and shared diagnostics.
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            entry_type=DeviceEntryType.SERVICE,
            manufacturer=MANUFACTURER,
            name=entry.title,
            model="Energy contract",
            configuration_url="https://mijn.budgetthuis.nl",
        )
