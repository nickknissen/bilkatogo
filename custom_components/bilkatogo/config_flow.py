from collections.abc import Mapping
from typing import Any

import probatio
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.helpers.httpx_client import create_async_httpx_client
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from bilkatogo import (
    REQUEST_TIMEOUT_SECONDS,
    BilkaBusyError,
    BilkaToGoClient,
    BilkaToGoError,
    BilkaUnreachableError,
    InvalidCredentialsError,
)

from .const import DOMAIN, LOGGER

PASSWORD_SELECTOR = TextSelector(
    TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
)
USER_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_EMAIL): TextSelector(
            TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")
        ),
        probatio.Required(CONF_PASSWORD): PASSWORD_SELECTOR,
    }
)
REAUTH_SCHEMA = probatio.Schema({probatio.Required(CONF_PASSWORD): PASSWORD_SELECTOR})


class BilkaToGoConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1
    MINOR_VERSION = 2

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            email = user_input[CONF_EMAIL].strip()
            password = user_input[CONF_PASSWORD]
            await self.async_set_unique_id(email.lower())
            self._abort_if_unique_id_configured()
            errors = await self._async_try_login(email, password)
            if not errors:
                return self.async_create_entry(
                    title=email, data={CONF_EMAIL: email, CONF_PASSWORD: password}
                )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        email = entry.data[CONF_EMAIL]
        errors: dict[str, str] = {}
        if user_input is not None:
            password = user_input[CONF_PASSWORD]
            errors = await self._async_try_login(email, password)
            if not errors:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: password}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=REAUTH_SCHEMA,
            description_placeholders={"email": email},
            errors=errors,
        )

    async def _async_try_login(self, email: str, password: str) -> dict[str, str]:
        client = BilkaToGoClient(
            email,
            password,
            httpx_client=create_async_httpx_client(self.hass, timeout=REQUEST_TIMEOUT_SECONDS),
        )
        try:
            await client.login()
        except InvalidCredentialsError:
            return {"base": "invalid_auth"}
        except (BilkaUnreachableError, BilkaBusyError):
            return {"base": "cannot_connect"}
        except BilkaToGoError:
            LOGGER.exception("Unexpected answer from Bilka while logging in")
            return {"base": "unknown"}
        return {}
