"""Config, reauth and options flow.

We do NOT use HA's OAuth2 framework: Budget Thuis uses a custom app-scheme
redirect reached by replaying a login form, which that framework cannot drive.
Instead we collect username/password, run the PKCE flow in the client, and store
only the refresh token.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, override

from aiobudgetthuis import (
    BudgetThuisAuthError,
    BudgetThuisClient,
    BudgetThuisConnectionError,
)
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_NAME, CONF_PASSWORD, CONF_TOKEN, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)
import voluptuous as vol

from .const import (
    CONF_CONTRACT_ID,
    CONF_PRICE_TYPE,
    CONF_UPDATE_INTERVAL,
    DEFAULT_PRICE_TYPE,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

_LOGGER = logging.getLogger(__name__)


class BudgetThuisConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the Budget Thuis config flow."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize per-flow state carried between steps."""
        self._username: str | None = None
        self._refresh_token: str | None = None
        # Maps contract id to a human-readable label for the picker.
        self._contracts: dict[str, str] = {}
        # Maps contract id to its type, for a neutral default entry title.
        self._contract_types: dict[str, str] = {}

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect credentials, log in, and discover contracts."""
        errors: dict[str, str] = {}
        if user_input is not None:
            client = BudgetThuisClient(async_get_clientsession(self.hass))
            try:
                tokens = await client.login(
                    user_input[CONF_USERNAME], user_input[CONF_PASSWORD]
                )
                contracts = await client.async_get_contracts(tokens.access_token)
            except BudgetThuisAuthError:
                errors["base"] = "invalid_auth"
            except BudgetThuisConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during Budget Thuis setup")
                errors["base"] = "unknown"
            else:
                self._username = user_input[CONF_USERNAME]
                self._refresh_token = tokens.refresh_token
                # Prefer active contracts; fall back to all if none are marked active.
                usable = [c for c in contracts if c.is_active] or contracts
                self._contracts = {c.id: c.label for c in usable}
                self._contract_types = {c.id: c.type for c in usable}
                if not self._contracts:
                    errors["base"] = "no_contracts"
                else:
                    # Always show the picker so the user can choose and/or rename.
                    return await self.async_step_select_contract()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME, default=self._username or ""): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
        )

    async def async_step_select_contract(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user pick a contract and optionally alias it."""
        if user_input is not None:
            return await self._async_finish(
                user_input[CONF_CONTRACT_ID], (user_input.get(CONF_NAME) or "").strip()
            )
        first = next(iter(self._contracts))
        return self.async_show_form(
            step_id="select_contract",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_CONTRACT_ID, default=first): vol.In(
                        self._contracts
                    ),
                    vol.Optional(CONF_NAME, default=""): str,
                }
            ),
        )

    async def _async_finish(self, contract_id: str, name: str = "") -> ConfigFlowResult:
        await self.async_set_unique_id(contract_id)
        self._abort_if_unique_id_configured()
        # Deliberately neutral default: the entry title becomes the device name
        # and thus every entity_id. The address-based contract label would leak
        # the user's street into automations, logs, and screenshots.
        contract_type = self._contract_types.get(contract_id, "")
        title = name or f"Budget Thuis {contract_type}".strip()
        return self.async_create_entry(
            title=title,
            data={
                CONF_USERNAME: self._username,
                CONF_CONTRACT_ID: contract_id,
                CONF_TOKEN: self._refresh_token,
            },
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Start reauth after the refresh token was rejected."""
        self._username = entry_data[CONF_USERNAME]
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the password again and store the fresh refresh token."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            client = BudgetThuisClient(async_get_clientsession(self.hass))
            try:
                tokens = await client.login(
                    self._username or entry.data[CONF_USERNAME],
                    user_input[CONF_PASSWORD],
                )
                contracts = await client.async_get_contracts(tokens.access_token)
            except BudgetThuisAuthError:
                errors["base"] = "invalid_auth"
            except BudgetThuisConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during Budget Thuis reauth")
                errors["base"] = "unknown"
            else:
                if entry.data[CONF_CONTRACT_ID] not in {c.id for c in contracts}:
                    return self.async_abort(reason="reauth_account_mismatch")
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_TOKEN: tokens.refresh_token}
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            errors=errors,
            description_placeholders={"username": self._username or ""},
        )

    @staticmethod
    @callback
    @override
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow handler."""
        return BudgetThuisOptionsFlow()


class BudgetThuisOptionsFlow(OptionsFlowWithReload):
    """Handle the update-interval and price-type options.

    OptionsFlowWithReload reloads the entry when options change, without an
    update listener that would also fire on runtime token rotation.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and store the options form."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_UPDATE_INTERVAL,
                        default=options.get(
                            CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL
                        ),
                    ): vol.All(
                        NumberSelector(
                            NumberSelectorConfig(
                                min=5,
                                max=360,
                                step=1,
                                mode=NumberSelectorMode.BOX,
                                unit_of_measurement="min",
                            )
                        ),
                        vol.Coerce(int),
                    ),
                    vol.Required(
                        CONF_PRICE_TYPE,
                        default=options.get(CONF_PRICE_TYPE, DEFAULT_PRICE_TYPE),
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=["gross", "net"],
                            mode=SelectSelectorMode.DROPDOWN,
                            translation_key="price_type",
                        )
                    ),
                }
            ),
        )
