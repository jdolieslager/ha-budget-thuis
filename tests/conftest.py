"""Common fixtures for the Budget Thuis integration tests.

All fixture data is fictional: no real credentials, tokens, addresses,
contract ids, or other personal data appear anywhere in the test suite.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

from aiobudgetthuis import (
    Contract,
    ContractInfo,
    FreeEnergyStatus,
    HourlyTariffDetails,
    Mandate,
    MonthlyAmount,
    Tokens,
    UsageSummary,
)
from aiobudgetthuis.models import usage_days_from_details
from homeassistant.const import CONF_TOKEN, CONF_USERNAME
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    load_json_array_fixture,
    load_json_object_fixture,
)
from pytest_homeassistant_custom_component.syrupy import HomeAssistantSnapshotExtension

from custom_components.budget_thuis.const import CONF_CONTRACT_ID, DOMAIN

if TYPE_CHECKING:
    from collections.abc import Generator

    from homeassistant.core import HomeAssistant
    from syrupy.assertion import SnapshotAssertion

TEST_CONTRACT_ID = "12345678"
TEST_CONTRACT_LABEL = "Voorbeeldstraat 12, Teststad - Energy"
TEST_ENTRY_ID = "budget_thuis_test_entry"
TEST_REFRESH_TOKEN = "test-refresh-token"
TEST_USERNAME = "user@example.com"


def fresh_tokens(*_args: Any, **_kwargs: Any) -> Tokens:
    """Build a token pair valid for one hour from "now" (frozen or real)."""
    return Tokens(
        access_token="test-access-token",
        refresh_token=TEST_REFRESH_TOKEN,
        expires_at=time.time() + 3600,
    )


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Let the harness load integrations from custom_components/."""


@pytest.fixture
def snapshot(snapshot: SnapshotAssertion) -> SnapshotAssertion:
    """Serialize HA states and registry entries the way core snapshots do."""
    return snapshot.use_extension(HomeAssistantSnapshotExtension)


@pytest.fixture
def entity_registry_enabled_by_default() -> Generator[None]:
    """Enable default-disabled entities so snapshots cover every entity."""
    with patch(
        "homeassistant.helpers.entity.Entity.entity_registry_enabled_default",
        PropertyMock(return_value=True),
    ):
        yield


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return a config entry as the flow would have created it."""
    return MockConfigEntry(
        domain=DOMAIN,
        entry_id=TEST_ENTRY_ID,
        title=TEST_CONTRACT_LABEL,
        unique_id=TEST_CONTRACT_ID,
        data={
            CONF_USERNAME: TEST_USERNAME,
            CONF_CONTRACT_ID: TEST_CONTRACT_ID,
            CONF_TOKEN: TEST_REFRESH_TOKEN,
        },
    )


@pytest.fixture
def mock_setup_entry() -> Generator[AsyncMock]:
    """Short-circuit entry setup for flow tests that don't need the platforms."""
    with patch(
        "custom_components.budget_thuis.async_setup_entry", return_value=True
    ) as mock:
        yield mock


@pytest.fixture
def mock_client() -> Generator[MagicMock]:
    """Patch BudgetThuisClient everywhere the integration imports it.

    One autospecced instance is shared by the config flow and the TokenManager,
    mirroring production where both construct a client around the HA session.
    Responses come from redacted JSON fixtures parsed through the real models.
    """
    with (
        patch(
            "custom_components.budget_thuis.coordinator.BudgetThuisClient",
            autospec=True,
        ) as client_cls,
        patch(
            "custom_components.budget_thuis.config_flow.BudgetThuisClient",
            new=client_cls,
        ),
    ):
        client = client_cls.return_value
        client.login.side_effect = fresh_tokens
        client.refresh.side_effect = fresh_tokens
        client.async_get_contracts.return_value = [
            Contract.from_dict(c) for c in load_json_array_fixture("contracts.json")
        ]
        client.hourly_tariff.return_value = HourlyTariffDetails.from_dict(
            load_json_object_fixture("hourly_tariff.json")
        )
        client.monthly_amount.return_value = MonthlyAmount.from_dict(
            load_json_object_fixture("monthly_amount.json")
        )
        client.usage_summary.return_value = UsageSummary.from_days(
            usage_days_from_details(load_json_object_fixture("usage_details.json"))
        )
        client.free_energy_status.return_value = FreeEnergyStatus.from_dict(
            load_json_object_fixture("free_energy.json")
        )
        client.contract_info.return_value = ContractInfo.from_dict(
            load_json_object_fixture("contract_info.json")
        )
        client.daily_reading_mandate.return_value = Mandate.from_dict(
            load_json_object_fixture("mandate.json")
        )
        yield client


@pytest.fixture
async def init_integration(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
) -> MockConfigEntry:
    """Set up the integration with the mocked client and return the entry."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    return mock_config_entry
