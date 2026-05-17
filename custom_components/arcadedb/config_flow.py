"""Config flow for ArcadeDB."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import (
    CONF_PASSWORD,
    CONF_URL,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import (
    ArcadeDBAuthError,
    ArcadeDBClient,
    ArcadeDBClientConfig,
    ArcadeDBError,
    ArcadeDBTransientError,
)
from .const import (
    CONF_BATCH_SIZE,
    CONF_DATABASE,
    CONF_FLUSH_INTERVAL,
    CONF_PRECISION,
    CONF_RETRY_COUNT,
    DEFAULT_BATCH_SIZE,
    DEFAULT_DATABASE,
    DEFAULT_FLUSH_INTERVAL,
    DEFAULT_PRECISION,
    DEFAULT_RETRY_COUNT,
    DOMAIN,
    PRECISION_OPTIONS,
)

_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_URL, default="http://localhost:2480"): str,
        vol.Required(CONF_DATABASE, default=DEFAULT_DATABASE): str,
        vol.Optional(CONF_USERNAME): str,
        vol.Optional(CONF_PASSWORD): str,
        vol.Required(CONF_VERIFY_SSL, default=True): bool,
        vol.Required(CONF_PRECISION, default=DEFAULT_PRECISION): vol.In(
            PRECISION_OPTIONS
        ),
        vol.Required(CONF_BATCH_SIZE, default=DEFAULT_BATCH_SIZE): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=5000)
        ),
        vol.Required(CONF_FLUSH_INTERVAL, default=DEFAULT_FLUSH_INTERVAL): vol.All(
            vol.Coerce(float), vol.Range(min=0.1, max=3600)
        ),
        vol.Required(CONF_RETRY_COUNT, default=DEFAULT_RETRY_COUNT): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=20)
        ),
    }
)


async def _validate_connection(
    hass: HomeAssistant,
    data: dict[str, Any],
) -> dict[str, str]:
    session = async_get_clientsession(hass, verify_ssl=data[CONF_VERIFY_SSL])
    client = ArcadeDBClient(
        session,
        ArcadeDBClientConfig(
            url=data[CONF_URL],
            database=data[CONF_DATABASE],
            username=data.get(CONF_USERNAME),
            password=data.get(CONF_PASSWORD),
            precision=data[CONF_PRECISION],
            verify_ssl=data[CONF_VERIFY_SSL],
        ),
    )

    try:
        await client.async_ping()
        if not await client.async_database_exists():
            return {"base": "invalid_database"}
    except ArcadeDBAuthError:
        return {"base": "invalid_auth"}
    except ArcadeDBTransientError:
        return {"base": "cannot_connect"}
    except ArcadeDBError:
        return {"base": "invalid_config"}
    except Exception:
        return {"base": "unknown"}

    return {}


class ArcadeDBConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle an ArcadeDB config flow."""

    VERSION = 1

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Handle the initial step."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        errors: dict[str, str] = {}
        if user_input is not None:
            errors = await _validate_connection(self.hass, user_input)
            if not errors:
                return self.async_create_entry(
                    title=f"{user_input[CONF_DATABASE]} ({user_input[CONF_URL]})",
                    data=user_input,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(_USER_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_import(
        self,
        import_data: dict[str, Any],
    ) -> config_entries.ConfigFlowResult:
        """Import YAML configuration."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        data = {
            CONF_VERIFY_SSL: True,
            CONF_PRECISION: DEFAULT_PRECISION,
            CONF_BATCH_SIZE: DEFAULT_BATCH_SIZE,
            CONF_FLUSH_INTERVAL: DEFAULT_FLUSH_INTERVAL,
            CONF_RETRY_COUNT: DEFAULT_RETRY_COUNT,
            **import_data,
        }
        errors = await _validate_connection(self.hass, data)
        if errors:
            return self.async_abort(reason=errors["base"])

        return self.async_create_entry(
            title=f"{data[CONF_DATABASE]} ({data[CONF_URL]})",
            data=data,
        )

