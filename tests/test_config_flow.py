"""Tests for the Budget Thuis config, reauth, and options flows."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from aiobudgetthuis import BudgetThuisAuthError, BudgetThuisConnectionError, Tokens
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_NAME, CONF_PASSWORD, CONF_TOKEN, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResultType, InvalidData
import pytest

from custom_components.budget_thuis.const import (
    CONF_CONTRACT_ID,
    CONF_PRICE_TYPE,
    CONF_UPDATE_INTERVAL,
    DOMAIN,
)

from .conftest import TEST_CONTRACT_ID, TEST_REFRESH_TOKEN, TEST_USERNAME

if TYPE_CHECKING:
    from unittest.mock import AsyncMock, MagicMock

    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

TEST_PASSWORD = "correct-horse-battery"


async def _start_user_flow(hass: HomeAssistant) -> dict[str, Any]:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {}
    return result


async def _submit_credentials(hass: HomeAssistant, flow_id: str) -> dict[str, Any]:
    return await hass.config_entries.flow.async_configure(
        flow_id, {CONF_USERNAME: TEST_USERNAME, CONF_PASSWORD: TEST_PASSWORD}
    )


async def test_full_flow_with_alias(
    hass: HomeAssistant, mock_client: MagicMock, mock_setup_entry: AsyncMock
) -> None:
    """The happy path creates an entry titled with the user's alias."""
    result = await _start_user_flow(hass)
    result = await _submit_credentials(hass, result["flow_id"])

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "select_contract"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_CONTRACT_ID: TEST_CONTRACT_ID, CONF_NAME: "Home Energy"},
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Home Energy"
    # Exactly these keys: the refresh token, never the password.
    assert result["data"] == {
        CONF_USERNAME: TEST_USERNAME,
        CONF_CONTRACT_ID: TEST_CONTRACT_ID,
        CONF_TOKEN: TEST_REFRESH_TOKEN,
    }
    assert result["result"].unique_id == TEST_CONTRACT_ID
    assert len(mock_setup_entry.mock_calls) == 1


@pytest.mark.parametrize("alias_input", [{}, {CONF_NAME: "   "}])
async def test_full_flow_without_alias(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_setup_entry: AsyncMock,
    alias_input: dict[str, str],
) -> None:
    """An empty or whitespace alias falls back to a neutral type-based title."""
    result = await _start_user_flow(hass)
    result = await _submit_credentials(hass, result["flow_id"])
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CONTRACT_ID: TEST_CONTRACT_ID, **alias_input}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Budget Thuis Energy"


@pytest.mark.parametrize(
    ("side_effect", "error"),
    [
        (BudgetThuisAuthError("bad credentials"), "invalid_auth"),
        (BudgetThuisConnectionError("timeout"), "cannot_connect"),
        (RuntimeError("boom"), "unknown"),
    ],
)
async def test_user_step_errors_and_recovery(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_setup_entry: AsyncMock,
    side_effect: Exception,
    error: str,
) -> None:
    """A failing login shows the mapped error; a retry then succeeds."""
    mock_client.login.side_effect = side_effect

    result = await _start_user_flow(hass)
    result = await _submit_credentials(hass, result["flow_id"])

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": error}

    mock_client.login.side_effect = None
    mock_client.login.return_value = Tokens(
        access_token="test-access-token",
        refresh_token=TEST_REFRESH_TOKEN,
        expires_at=time.time() + 3600,
    )
    result = await _submit_credentials(hass, result["flow_id"])
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "select_contract"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CONTRACT_ID: TEST_CONTRACT_ID}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_no_contracts_and_recovery(
    hass: HomeAssistant, mock_client: MagicMock, mock_setup_entry: AsyncMock
) -> None:
    """An account without contracts errors; it recovers once contracts appear."""
    contracts = mock_client.async_get_contracts.return_value
    mock_client.async_get_contracts.return_value = []

    result = await _start_user_flow(hass)
    result = await _submit_credentials(hass, result["flow_id"])

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "no_contracts"}

    mock_client.async_get_contracts.return_value = contracts
    result = await _submit_credentials(hass, result["flow_id"])
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "select_contract"


async def test_inactive_contracts_are_offered(
    hass: HomeAssistant, mock_client: MagicMock, mock_setup_entry: AsyncMock
) -> None:
    """Without any active contract, all contracts remain selectable."""
    contracts = mock_client.async_get_contracts.return_value
    mock_client.async_get_contracts.return_value = [
        c for c in contracts if not c.is_active
    ]

    result = await _start_user_flow(hass)
    result = await _submit_credentials(hass, result["flow_id"])
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "select_contract"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CONTRACT_ID: "87654321"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == "87654321"


async def test_duplicate_contract_aborts(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_setup_entry: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Selecting an already-configured contract aborts the flow."""
    mock_config_entry.add_to_hass(hass)

    result = await _start_user_flow(hass)
    result = await _submit_credentials(hass, result["flow_id"])
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CONTRACT_ID: TEST_CONTRACT_ID}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


@pytest.mark.parametrize(
    ("side_effect", "error"),
    [
        (BudgetThuisAuthError("bad credentials"), "invalid_auth"),
        (BudgetThuisConnectionError("timeout"), "cannot_connect"),
        (RuntimeError("boom"), "unknown"),
    ],
)
@pytest.mark.usefixtures("mock_setup_entry")
async def test_reauth_flow(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    side_effect: Exception,
    error: str,
) -> None:
    """Reauth shows errors, recovers, rotates the token, and never duplicates."""
    mock_config_entry.add_to_hass(hass)

    result = await mock_config_entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    mock_client.login.side_effect = side_effect
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "wrong-password"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    assert result["errors"] == {"base": error}

    mock_client.login.side_effect = None
    mock_client.login.return_value = Tokens(
        access_token="new-access-token",
        refresh_token="new-refresh-token",
        expires_at=time.time() + 3600,
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: TEST_PASSWORD}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert mock_config_entry.data == {
        CONF_USERNAME: TEST_USERNAME,
        CONF_CONTRACT_ID: TEST_CONTRACT_ID,
        CONF_TOKEN: "new-refresh-token",
    }
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


@pytest.mark.usefixtures("mock_setup_entry")
async def test_reauth_account_mismatch_aborts(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Reauth with an account that lacks the entry's contract keeps the token."""
    mock_config_entry.add_to_hass(hass)
    mock_client.async_get_contracts.return_value = [
        c
        for c in mock_client.async_get_contracts.return_value
        if c.id != TEST_CONTRACT_ID
    ]

    result = await mock_config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: TEST_PASSWORD}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_account_mismatch"
    # The wrong account's token must never replace the stored one.
    assert mock_config_entry.data[CONF_TOKEN] == TEST_REFRESH_TOKEN
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


async def test_options_flow(
    hass: HomeAssistant,
    mock_setup_entry: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """The options flow round-trips the update interval and price type."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_UPDATE_INTERVAL: 60, CONF_PRICE_TYPE: "net"}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert mock_config_entry.options == {
        CONF_UPDATE_INTERVAL: 60,
        CONF_PRICE_TYPE: "net",
    }


@pytest.mark.parametrize("interval", [5, 360])
@pytest.mark.usefixtures("mock_setup_entry")
async def test_options_interval_boundaries_accepted(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    interval: int,
) -> None:
    """The interval limits are inclusive and the value is stored as an int."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_UPDATE_INTERVAL: interval, CONF_PRICE_TYPE: "gross"}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    stored = mock_config_entry.options[CONF_UPDATE_INTERVAL]
    assert stored == interval
    assert isinstance(stored, int)


@pytest.mark.parametrize("interval", [0, 4, 361])
@pytest.mark.usefixtures("mock_setup_entry")
async def test_options_interval_out_of_range_rejected(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    interval: int,
) -> None:
    """Out-of-range intervals fail selector validation and store nothing."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(
            result["flow_id"],
            {CONF_UPDATE_INTERVAL: interval, CONF_PRICE_TYPE: "gross"},
        )

    assert CONF_UPDATE_INTERVAL not in mock_config_entry.options
