"""Home Assistant runtime glue for the ArcadeDB integration."""

from __future__ import annotations

import json
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
    CONF_GRAPH,
    CONF_GRAPH_ENABLED,
    CONF_GRAPH_INCLUDE_STATE_SNAPSHOT,
    CONF_GRAPH_SYNC_INTERVAL,
    CONF_IGNORE_ATTRIBUTES,
    CONF_INCLUDE_ATTRIBUTES,
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
    DEFAULT_GRAPH_SYNC_INTERVAL,
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
from .graph_exporter import ArcadeDBGraphExporter, GraphExporterConfig
from .graph_model import (
    EDGE_CONFIG_ENTRY_DOMAIN,
    EDGE_DEVICE_AREA,
    EDGE_DEVICE_CONFIG_ENTRY,
    EDGE_DEVICE_VIA_DEVICE,
    EDGE_ENTITY_AREA,
    EDGE_ENTITY_CONFIG_ENTRY,
    EDGE_ENTITY_DEVICE,
    EDGE_ENTITY_DOMAIN,
    VERTEX_AREA,
    VERTEX_CONFIG_ENTRY,
    VERTEX_DEVICE,
    VERTEX_DOMAIN,
    VERTEX_ENTITY,
    GraphEdge,
    GraphSnapshot,
    GraphVertex,
    domain_vertex,
)
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
        vol.Optional(CONF_INCLUDE_ATTRIBUTES): cv.boolean,
        vol.Optional(CONF_IGNORE_ATTRIBUTES, default=[]): vol.All(
            cv.ensure_list, [cv.string]
        ),
    }
)

_GRAPH_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_GRAPH_ENABLED, default=False): cv.boolean,
        vol.Optional(
            CONF_GRAPH_SYNC_INTERVAL, default=DEFAULT_GRAPH_SYNC_INTERVAL
        ): vol.All(vol.Coerce(float), vol.Range(min=30, max=86400)),
        vol.Optional(CONF_GRAPH_INCLUDE_STATE_SNAPSHOT, default=True): cv.boolean,
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
        vol.Optional(CONF_INCLUDE_ATTRIBUTES, default=True): cv.boolean,
        vol.Optional(CONF_IGNORE_ATTRIBUTES, default=[]): vol.All(
            cv.ensure_list, [cv.string]
        ),
        vol.Optional(CONF_GRAPH, default={}): _GRAPH_SCHEMA,
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
    graph_exporter: ArcadeDBGraphExporter | None = None
    graph_config = data[CONF_GRAPH]
    if graph_config[CONF_GRAPH_ENABLED]:
        graph_exporter = ArcadeDBGraphExporter(
            client.async_command,
            lambda: _async_graph_snapshot(
                hass,
                include_state_snapshot=graph_config[
                    CONF_GRAPH_INCLUDE_STATE_SNAPSHOT
                ],
            ),
            GraphExporterConfig(
                sync_interval=graph_config[CONF_GRAPH_SYNC_INTERVAL],
                max_retries=data[CONF_RETRY_COUNT],
                retry_interval=data[CONF_RETRY_INTERVAL],
            ),
            logger=_LOGGER,
        )
        await graph_exporter.async_start()

    @callback
    def _handle_state_change(event: Event) -> None:
        state = event.data.get(EVENT_NEW_STATE)
        if state is not None:
            exporter.queue_state(state, event.time_fired)

    unsubscribe = hass.bus.async_listen(EVENT_STATE_CHANGED, _handle_state_change)
    hass.data.setdefault(DOMAIN, {})[runtime_id] = {
        "exporter": exporter,
        "graph_exporter": graph_exporter,
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
    graph_exporter: ArcadeDBGraphExporter | None = runtime.get("graph_exporter")
    if graph_exporter is not None:
        await graph_exporter.async_stop()
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
        include_attributes=data[CONF_INCLUDE_ATTRIBUTES],
        ignore_attributes=tuple(data[CONF_IGNORE_ATTRIBUTES]),
        component_config=data[CONF_COMPONENT_CONFIG],
        component_config_domain=data[CONF_COMPONENT_CONFIG_DOMAIN],
        component_config_glob=data[CONF_COMPONENT_CONFIG_GLOB],
    )


async def _async_graph_snapshot(
    hass: HomeAssistant,
    *,
    include_state_snapshot: bool,
) -> GraphSnapshot:
    """Build a graph snapshot from Home Assistant registries."""
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    vertices: list[GraphVertex] = []
    edges: list[GraphEdge] = []
    domain_keys: set[str] = set()
    config_entry_keys: set[str] = set()

    def add_domain(domain: str) -> None:
        if domain not in domain_keys:
            domain_keys.add(domain)
            vertices.append(domain_vertex(domain))

    for entry in hass.config_entries.async_entries():
        domain = str(entry.domain)
        add_domain(domain)
        key = _config_entry_key(entry.entry_id)
        config_entry_keys.add(key)
        vertices.append(
            GraphVertex(
                VERTEX_CONFIG_ENTRY,
                key,
                {
                    "kind": "config_entry",
                    "entry_id": entry.entry_id,
                    "domain": domain,
                    "title": entry.title,
                    "source": _string_value(getattr(entry, "source", None)),
                    "state": _string_value(getattr(entry, "state", None)),
                    "disabled_by": _string_value(
                        getattr(entry, "disabled_by", None)
                    ),
                    "unique_id": getattr(entry, "unique_id", None),
                },
            )
        )
        edges.append(
            GraphEdge(
                EDGE_CONFIG_ENTRY_DOMAIN,
                VERTEX_CONFIG_ENTRY,
                key,
                VERTEX_DOMAIN,
                _domain_key(domain),
            )
        )

    area_registry = ar.async_get(hass)
    for area in getattr(area_registry, "areas", {}).values():
        key = _area_key(area.id)
        vertices.append(
            GraphVertex(
                VERTEX_AREA,
                key,
                {
                    "kind": "area",
                    "area_id": area.id,
                    "name": area.name,
                    "floor_id": getattr(area, "floor_id", None),
                    "aliases": _sorted_strings(getattr(area, "aliases", None)),
                    "labels": _sorted_strings(getattr(area, "labels", None)),
                },
            )
        )

    device_registry = dr.async_get(hass)
    device_area_by_id: dict[str, str | None] = {}
    for device in getattr(device_registry, "devices", {}).values():
        device_id = str(device.id)
        device_area_by_id[device_id] = getattr(device, "area_id", None)
        config_entry_ids = _sorted_strings(getattr(device, "config_entries", None))
        key = _device_key(device_id)
        vertices.append(
            GraphVertex(
                VERTEX_DEVICE,
                key,
                {
                    "kind": "device",
                    "device_id": device_id,
                    "name": getattr(device, "name", None),
                    "name_by_user": getattr(device, "name_by_user", None),
                    "display_name": getattr(device, "name_by_user", None)
                    or getattr(device, "name", None),
                    "area_id": getattr(device, "area_id", None),
                    "config_entries": config_entry_ids,
                    "configuration_url": _string_value(
                        getattr(device, "configuration_url", None)
                    ),
                    "connections": _pair_strings(getattr(device, "connections", None)),
                    "disabled_by": _string_value(getattr(device, "disabled_by", None)),
                    "entry_type": _string_value(getattr(device, "entry_type", None)),
                    "hw_version": getattr(device, "hw_version", None),
                    "identifiers": _pair_strings(getattr(device, "identifiers", None)),
                    "manufacturer": getattr(device, "manufacturer", None),
                    "model": getattr(device, "model", None),
                    "model_id": getattr(device, "model_id", None),
                    "serial_number": getattr(device, "serial_number", None),
                    "sw_version": getattr(device, "sw_version", None),
                    "via_device_id": getattr(device, "via_device_id", None),
                },
            )
        )

        area_id = getattr(device, "area_id", None)
        if area_id:
            edges.append(
                GraphEdge(
                    EDGE_DEVICE_AREA,
                    VERTEX_DEVICE,
                    key,
                    VERTEX_AREA,
                    _area_key(area_id),
                )
            )
        for entry_id in config_entry_ids:
            entry_key = _config_entry_key(entry_id)
            if entry_key in config_entry_keys:
                edges.append(
                    GraphEdge(
                        EDGE_DEVICE_CONFIG_ENTRY,
                        VERTEX_DEVICE,
                        key,
                        VERTEX_CONFIG_ENTRY,
                        entry_key,
                    )
                )
        via_device_id = getattr(device, "via_device_id", None)
        if via_device_id:
            edges.append(
                GraphEdge(
                    EDGE_DEVICE_VIA_DEVICE,
                    VERTEX_DEVICE,
                    key,
                    VERTEX_DEVICE,
                    _device_key(via_device_id),
                )
            )

    states_by_entity_id = {
        state.entity_id: state for state in hass.states.async_all()
    }
    entity_registry = er.async_get(hass)
    seen_entity_ids: set[str] = set()
    for entity in getattr(entity_registry, "entities", {}).values():
        entity_id = str(entity.entity_id)
        seen_entity_ids.add(entity_id)
        vertices.append(
            _entity_vertex(
                entity_id,
                source="entity_registry",
                platform=getattr(entity, "platform", None),
                unique_id=getattr(entity, "unique_id", None),
                original_name=getattr(entity, "original_name", None),
                name=getattr(entity, "name", None),
                device_id=getattr(entity, "device_id", None),
                area_id=getattr(entity, "area_id", None),
                config_entry_id=getattr(entity, "config_entry_id", None),
                disabled_by=_string_value(getattr(entity, "disabled_by", None)),
                hidden_by=_string_value(getattr(entity, "hidden_by", None)),
                entity_category=_string_value(
                    getattr(entity, "entity_category", None)
                ),
                labels=_sorted_strings(getattr(entity, "labels", None)),
                state=states_by_entity_id.get(entity_id)
                if include_state_snapshot
                else None,
                device_area_id=device_area_by_id.get(
                    str(getattr(entity, "device_id", ""))
                ),
            )
        )
        _entity_edges(
            edges,
            entity_id=entity_id,
            device_id=getattr(entity, "device_id", None),
            area_id=getattr(entity, "area_id", None)
            or device_area_by_id.get(str(getattr(entity, "device_id", ""))),
            config_entry_id=getattr(entity, "config_entry_id", None),
            known_config_entries=config_entry_keys,
        )
        add_domain(entity_id.split(".", 1)[0])

    for entity_id, state in states_by_entity_id.items():
        if entity_id in seen_entity_ids:
            continue
        vertices.append(
            _entity_vertex(
                entity_id,
                source="state_machine",
                state=state if include_state_snapshot else None,
            )
        )
        _entity_edges(
            edges,
            entity_id=entity_id,
            device_id=None,
            area_id=None,
            config_entry_id=None,
            known_config_entries=config_entry_keys,
        )
        add_domain(entity_id.split(".", 1)[0])

    return GraphSnapshot(tuple(vertices), tuple(edges))


def _entity_vertex(
    entity_id: str,
    *,
    source: str,
    platform: str | None = None,
    unique_id: str | None = None,
    original_name: str | None = None,
    name: str | None = None,
    device_id: str | None = None,
    area_id: str | None = None,
    config_entry_id: str | None = None,
    disabled_by: str | None = None,
    hidden_by: str | None = None,
    entity_category: str | None = None,
    labels: list[str] | None = None,
    state: Any | None = None,
    device_area_id: str | None = None,
) -> GraphVertex:
    domain, _, object_id = entity_id.partition(".")
    properties: dict[str, Any] = {
        "kind": "entity",
        "entity_id": entity_id,
        "domain": domain,
        "object_id": object_id,
        "source": source,
        "platform": platform,
        "unique_id": unique_id,
        "original_name": original_name,
        "name": name,
        "device_id": device_id,
        "area_id": area_id,
        "resolved_area_id": area_id or device_area_id,
        "config_entry_id": config_entry_id,
        "disabled_by": disabled_by,
        "hidden_by": hidden_by,
        "entity_category": entity_category,
        "labels": labels or [],
    }
    if state is not None:
        properties.update(_state_properties(state))
    return GraphVertex(VERTEX_ENTITY, _entity_key(entity_id), properties)


def _entity_edges(
    edges: list[GraphEdge],
    *,
    entity_id: str,
    device_id: str | None,
    area_id: str | None,
    config_entry_id: str | None,
    known_config_entries: set[str],
) -> None:
    entity_key = _entity_key(entity_id)
    domain = entity_id.split(".", 1)[0]
    edges.append(
        GraphEdge(
            EDGE_ENTITY_DOMAIN,
            VERTEX_ENTITY,
            entity_key,
            VERTEX_DOMAIN,
            _domain_key(domain),
        )
    )
    if device_id:
        edges.append(
            GraphEdge(
                EDGE_ENTITY_DEVICE,
                VERTEX_ENTITY,
                entity_key,
                VERTEX_DEVICE,
                _device_key(device_id),
            )
        )
    if area_id:
        edges.append(
            GraphEdge(
                EDGE_ENTITY_AREA,
                VERTEX_ENTITY,
                entity_key,
                VERTEX_AREA,
                _area_key(area_id),
            )
        )
    if config_entry_id and _config_entry_key(config_entry_id) in known_config_entries:
        edges.append(
            GraphEdge(
                EDGE_ENTITY_CONFIG_ENTRY,
                VERTEX_ENTITY,
                entity_key,
                VERTEX_CONFIG_ENTRY,
                _config_entry_key(config_entry_id),
            )
        )


def _state_properties(state: Any) -> dict[str, Any]:
    attributes = dict(getattr(state, "attributes", {}) or {})
    return {
        "state": str(getattr(state, "state", "")),
        "last_changed": _string_value(getattr(state, "last_changed", None)),
        "last_updated": _string_value(getattr(state, "last_updated", None)),
        "friendly_name": attributes.get("friendly_name"),
        "unit_of_measurement": attributes.get("unit_of_measurement"),
        "device_class": attributes.get("device_class"),
        "state_class": attributes.get("state_class"),
        "attributes_json": _json_limited(attributes),
    }


def _config_entry_key(entry_id: str) -> str:
    return f"config_entry:{entry_id}"


def _domain_key(domain: str) -> str:
    return f"domain:{domain}"


def _area_key(area_id: str) -> str:
    return f"area:{area_id}"


def _device_key(device_id: str) -> str:
    return f"device:{device_id}"


def _entity_key(entity_id: str) -> str:
    return f"entity:{entity_id}"


def _string_value(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)


def _sorted_strings(value: Any) -> list[str]:
    if not value:
        return []
    return sorted(str(item) for item in value)


def _pair_strings(value: Any) -> list[str]:
    if not value:
        return []
    return sorted(":".join(str(part) for part in item) for item in value)


def _json_limited(value: Any, limit: int = 8000) -> str:
    try:
        payload = json.dumps(value, default=str, sort_keys=True)
    except (TypeError, ValueError):
        payload = str(value)
    if len(payload) <= limit:
        return payload
    return f"{payload[:limit]}...<truncated>"
