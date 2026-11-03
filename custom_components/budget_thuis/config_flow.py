"""Config, reauth and options flow.

We do NOT use HA's OAuth2 framework: Budget Thuis uses a custom app-scheme
redirect, which that framework cannot drive. Budget Thuis no longer supports
passwords; it e-mails a one-time login link. The flow asks for the e-mail
address, has Budget Thuis send the link, and lets the user paste that link
(instead of clicking it). We then finish the PKCE flow ourselves and store only
the refresh token.
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
from homeassistant.const import CONF_NAME, CONF_TOKEN, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
import voluptuous as vol

from .auth import BudgetThuisInvalidLinkError, MagicLinkLogin
from .const import (
    CONF_CONTRACT_ID,
    CONF_LOGIN_LINK,
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
        self._login: MagicLinkLogin | None = None
        self._reauth = False
        # Maps contract id to a human-readable label for the picker.
        self._contracts: dict[str, str] = {}
        # Maps contract id to its type, for a neutral default entry title.
        self._contract_types: dict[str, str] = {}

    async def _async_request_link(self, email: str) -> str | None:
        """Ask Budget Thuis to e-mail a login link; return an error key or None."""
        self._login = MagicLinkLogin()
        try:
            await self._login.async_request_link(email)
        except BudgetThuisAuthError:
            return "invalid_email"
        except BudgetThuisConnectionError:
            return "cannot_connect"
        except Exception:
            _LOGGER.exception("Unexpected error requesting a Budget Thuis login link")
            return "unknown"
        return None

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the e-mail address and have a login link sent."""
        errors: dict[str, str] = {}
        if user_input is not None:
            email = user_input[CONF_USERNAME].strip()
            if error := await self._async_request_link(email):
                errors["base"] = error
            else:
                self._username = email
                return await self.async_step_link()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_USERNAME, default=self._username or ""
                    ): TextSelector(TextSelectorConfig(type=TextSelectorType.EMAIL)),
                }
            ),
            errors=errors,
        )

    async def async_step_link(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Accept the pasted login link and finish the login."""
        errors: dict[str, str] = {}
        if user_input is not None and self._login is not None:
            client = BudgetThuisClient(async_get_clientsession(self.hass))
            try:
                tokens = await self._login.async_complete(user_input[CONF_LOGIN_LINK])
                contracts = await client.async_get_contracts(tokens.access_token)
            except BudgetThuisInvalidLinkError:
                errors["base"] = "invalid_link"
            except BudgetThuisAuthError:
                errors["base"] = "invalid_auth"
            except BudgetThuisConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during Budget Thuis login")
                errors["base"] = "unknown"
            else:
                self._refresh_token = tokens.refresh_token
                if self._reauth:
                    entry = self._get_reauth_entry()
                    if entry.data[CONF_CONTRACT_ID] not in {c.id for c in contracts}:
                        return self.async_abort(reason="reauth_account_mismatch")
                    return self.async_update_reload_and_abort(
                        entry, data_updates={CONF_TOKEN: tokens.refresh_token}
                    )
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
            step_id="link",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_LOGIN_LINK): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.URL)
                    ),
                }
            ),
            errors=errors,
            description_placeholders={"username": self._username or ""},
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
        self._reauth = True
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm, then send a new login link to the stored e-mail address."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if error := await self._async_request_link(self._username or ""):
                errors["base"] = error
            else:
                return await self.async_step_link()

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({}),
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
