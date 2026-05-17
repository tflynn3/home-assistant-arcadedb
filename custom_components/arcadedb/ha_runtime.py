"""Home Assistant runtime glue for the ArcadeDB integration."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_EXCLUDE,
    CONF_INCLUDE,
    CONF_PASSWORD,
    CONF_URL,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    EVENT_STATE_CHANGED,
)
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .client import (
    ArcadeDBAuthError,
    ArcadeDBClient,
    ArcadeDBClientConfig,
    ArcadeDBError,
    ArcadeDBTransientError,
)
from .const import (
    CONF_BATCH_SIZE,
    CONF_COMPONENT_CONFIG,
    CONF_COMPONENT_CONFIG_DOMAIN,
    CONF_COMPONENT_CONFIG_GLOB,
    CONF_DATABASE,
    CONF_DEFAULT_MEASUREMENT,
    CONF_DEFAULT_TAGS,
    CONF_FLUSH_INTERVAL,
    CONF_IGNORE_ATTRIBUTES,
    CONF_MEASUREMENT_ATTR,
    CONF_OVERRIDE_MEASUREMENT,
    CONF_PRECISION,
    CONF_QUEUE_MAX_SIZE,
    CONF_RETRY_COUNT,
    CONF_RETRY_INTERVAL,
    CONF_TAGS_ATTRIBUTES,
    DEFAULT_BATCH_SIZE,
    DEFAULT_DATABASE,
    DEFAULT_FLUSH_INTERVAL,
    DEFAULT_MEASUREMENT_ATTR,
    DEFAULT_PRECISION,
    DEFAULT_QUEUE_MAX_SIZE,
    DEFAULT_RETRY_COUNT,
    DEFAULT_RETRY_INTERVAL,
    DOMAIN,
    EVENT_NEW_STATE,
    MEASUREMENT_ATTR_OPTIONS,
    PRECISION_OPTIONS,
)
from .exporter import ArcadeDBExporter
from .filtering import entity_filter_from_config
from .line_protocol import LineProtocolConfig, state_to_line

_LOGGER = logging.getLogger(__name__)

type ArcadeDBConfigEntry = ConfigEntry[dict[str, Any]]

_FILTER_SCHEMA = vol.Schema(
    {
        vol.Optional("entities", default=[]): vol.All(cv.ensure_list, [cv.entity_id]),
        vol.Optional("domains", default=[]): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional("entity_globs", default=[]): vol.All(
            cv.ensure_list, [cv.string]
        ),
    }
)

_CUSTOMIZE_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_OVERRIDE_MEASUREMENT): cv.string,
        vol.Optional(CONF_IGNORE_ATTRIBUTES, default=[]): vol.All(
            cv.ensure_list, [cv.string]
        ),
    }
)

_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_URL): cv.url,
        vol.Required(CONF_DATABASE, default=DEFAULT_DATABASE): cv.string,
        vol.Optional(CONF_USERNAME): cv.string,
        vol.Optional(CONF_PASSWORD): cv.string,
        vol.Optional(CONF_VERIFY_SSL, default=True): cv.boolean,
        vol.Optional(CONF_PRECISION, default=DEFAULT_PRECISION): vol.In(
            PRECISION_OPTIONS
        ),
        vol.Optional(CONF_BATCH_SIZE, default=DEFAULT_BATCH_SIZE): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=5000)
        ),
        vol.Optional(CONF_FLUSH_INTERVAL, default=DEFAULT_FLUSH_INTERVAL): vol.All(
            vol.Coerce(float), vol.Range(min=0.1, max=3600)
        ),
        vol.Optional(CONF_RETRY_COUNT, default=DEFAULT_RETRY_COUNT): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=20)
        ),
        vol.Optional(CONF_RETRY_INTERVAL, default=DEFAULT_RETRY_INTERVAL): vol.All(
            vol.Coerce(float), vol.Range(min=0, max=3600)
        ),
        vol.Optional(CONF_QUEUE_MAX_SIZE, default=DEFAULT_QUEUE_MAX_SIZE): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=1000000)
        ),
        vol.Optional(CONF_MEASUREMENT_ATTR, default=DEFAULT_MEASUREMENT_ATTR): vol.In(
            MEASUREMENT_ATTR_OPTIONS
        ),
        vol.Optional(CONF_DEFAULT_MEASUREMENT): cv.string,
        vol.Optional(CONF_OVERRIDE_MEASUREMENT): cv.string,
        vol.Optional(CONF_DEFAULT_TAGS, default={}): vol.Schema({cv.string: cv.string}),
        vol.Optional(CONF_TAGS_ATTRIBUTES, default=[]): vol.All(
            cv.ensure_list, [cv.string]
        ),
        vol.Optional(CONF_IGNORE_ATTRIBUTES, default=[]): vol.All(
            cv.ensure_list, [cv.string]
        ),
        vol.Optional(CONF_INCLUDE, default={}): _FILTER_SCHEMA,
        vol.Optional(CONF_EXCLUDE, default={}): _FILTER_SCHEMA,
        vol.Optional(CONF_COMPONENT_CONFIG, default={}): vol.Schema(
            {cv.entity_id: _CUSTOMIZE_SCHEMA}
        ),
        vol.Optional(CONF_COMPONENT_CONFIG_DOMAIN, default={}): vol.Schema(
            {cv.string: _CUSTOMIZE_SCHEMA}
        ),
        vol.Optional(CONF_COMPONENT_CONFIG_GLOB, default={}): vol.Schema(
            {cv.string: _CUSTOMIZE_SCHEMA}
        ),
    }
)

CONFIG_SCHEMA = vol.Schema({DOMAIN: _SCHEMA}, extra=vol.ALLOW_EXTRA)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up ArcadeDB from YAML."""
    if DOMAIN not in config:
        return True

    _LOGGER.info("Setting up ArcadeDB exporter from YAML")
    return await _async_setup_exporter(hass, "yaml", _SCHEMA(dict(config[DOMAIN])))


async def async_setup_entry(hass: HomeAssistant, entry: ArcadeDBConfigEntry) -> bool:
    """Set up ArcadeDB exporter from a config entry."""
    try:
        return await _async_setup_exporter(
            hass, entry.entry_id, _entry_config(entry), config_entry=True
        )
    except ArcadeDBTransientError as err:
        raise ConfigEntryNotReady(str(err)) from err


async def _async_setup_exporter(
    hass: HomeAssistant,
    runtime_id: str,
    data: dict[str, Any],
    *,
    config_entry: bool = False,
) -> bool:
    """Start an ArcadeDB exporter runtime."""
    session = async_get_clientsession(hass, verify_ssl=data[CONF_VERIFY_SSL])
    client = ArcadeDBClient(session, _client_config(data))

    try:
        await client.async_ping()
    except ArcadeDBTransientError as err:
        if config_entry:
            raise
        _LOGGER.error("ArcadeDB connection failed during YAML setup: %s", err)
        return False
    except ArcadeDBAuthError:
        _LOGGER.error("ArcadeDB authentication failed")
        return False
    except ArcadeDBError as err:
        _LOGGER.error("ArcadeDB connection failed: %s", err)
        return False

    line_config = _line_config(data)
    entity_filter = entity_filter_from_config(data)

    exporter = ArcadeDBExporter(
        client.async_write_lines,
        lambda state, time_fired: state_to_line(state, line_config, time_fired),
        entity_filter=entity_filter,
        batch_size=data[CONF_BATCH_SIZE],
        flush_interval=data[CONF_FLUSH_INTERVAL],
        max_retries=data[CONF_RETRY_COUNT],
        retry_interval=data[CONF_RETRY_INTERVAL],
        queue_max_size=data[CONF_QUEUE_MAX_SIZE],
        logger=_LOGGER,
    )
    await exporter.async_start()

    @callback
    def _handle_state_change(event: Event) -> None:
        state = event.data.get(EVENT_NEW_STATE)
        if state is not None:
            exporter.queue_state(state, event.time_fired)

    unsubscribe = hass.bus.async_listen(EVENT_STATE_CHANGED, _handle_state_change)
    hass.data.setdefault(DOMAIN, {})[runtime_id] = {
        "exporter": exporter,
        "unsubscribe": unsubscribe,
    }
    _LOGGER.info("ArcadeDB exporter started")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ArcadeDBConfigEntry) -> bool:
    """Unload ArcadeDB exporter."""
    runtime = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if runtime is None:
        return True

    unsubscribe: Callable[[], None] = runtime["unsubscribe"]
    unsubscribe()
    exporter: ArcadeDBExporter = runtime["exporter"]
    await exporter.async_stop()
    return True


def _entry_config(entry: ArcadeDBConfigEntry) -> dict[str, Any]:
    data = dict(entry.data)
    data.update(entry.options)
    return _SCHEMA(data)


def _client_config(data: dict[str, Any]) -> ArcadeDBClientConfig:
    return ArcadeDBClientConfig(
        url=data[CONF_URL],
        database=data[CONF_DATABASE],
        username=data.get(CONF_USERNAME),
        password=data.get(CONF_PASSWORD),
        precision=data[CONF_PRECISION],
        verify_ssl=data[CONF_VERIFY_SSL],
    )


def _line_config(data: dict[str, Any]) -> LineProtocolConfig:
    return LineProtocolConfig(
        precision=data[CONF_PRECISION],
        measurement_attr=data[CONF_MEASUREMENT_ATTR],
        default_measurement=data.get(CONF_DEFAULT_MEASUREMENT),
        override_measurement=data.get(CONF_OVERRIDE_MEASUREMENT),
        default_tags=data[CONF_DEFAULT_TAGS],
        tags_attributes=tuple(data[CONF_TAGS_ATTRIBUTES]),
        ignore_attributes=tuple(data[CONF_IGNORE_ATTRIBUTES]),
        component_config=data[CONF_COMPONENT_CONFIG],
        component_config_domain=data[CONF_COMPONENT_CONFIG_DOMAIN],
        component_config_glob=data[CONF_COMPONENT_CONFIG_GLOB],
    )
