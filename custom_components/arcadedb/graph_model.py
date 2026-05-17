"""ArcadeDB graph schema and command generation."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

VERTEX_CONFIG_ENTRY = "HAConfigEntry"
VERTEX_DOMAIN = "HADomain"
VERTEX_AREA = "HAArea"
VERTEX_DEVICE = "HADevice"
VERTEX_ENTITY = "HAEntity"

EDGE_CONFIG_ENTRY_DOMAIN = "HA_CONFIG_ENTRY_DOMAIN"
EDGE_DEVICE_CONFIG_ENTRY = "HA_DEVICE_CONFIG_ENTRY"
EDGE_ENTITY_CONFIG_ENTRY = "HA_ENTITY_CONFIG_ENTRY"
EDGE_DEVICE_AREA = "HA_DEVICE_AREA"
EDGE_ENTITY_AREA = "HA_ENTITY_AREA"
EDGE_ENTITY_DEVICE = "HA_ENTITY_DEVICE"
EDGE_ENTITY_DOMAIN = "HA_ENTITY_DOMAIN"
EDGE_DEVICE_VIA_DEVICE = "HA_DEVICE_VIA_DEVICE"

VERTEX_TYPES = (
    VERTEX_CONFIG_ENTRY,
    VERTEX_DOMAIN,
    VERTEX_AREA,
    VERTEX_DEVICE,
    VERTEX_ENTITY,
)

EDGE_TYPES = (
    EDGE_CONFIG_ENTRY_DOMAIN,
    EDGE_DEVICE_CONFIG_ENTRY,
    EDGE_ENTITY_CONFIG_ENTRY,
    EDGE_DEVICE_AREA,
    EDGE_ENTITY_AREA,
    EDGE_ENTITY_DEVICE,
    EDGE_ENTITY_DOMAIN,
    EDGE_DEVICE_VIA_DEVICE,
)

_IDENTIFIER = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")


@dataclass(frozen=True)
class GraphVertex:
    """A Home Assistant metadata vertex."""

    type_name: str
    key: str
    properties: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GraphEdge:
    """A Home Assistant metadata relationship."""

    type_name: str
    from_type: str
    from_key: str
    to_type: str
    to_key: str
    properties: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GraphSnapshot:
    """A full Home Assistant metadata graph snapshot."""

    vertices: tuple[GraphVertex, ...]
    edges: tuple[GraphEdge, ...]


def graph_schema_commands() -> tuple[str, ...]:
    """Return idempotent schema setup commands for the Home Assistant graph."""
    commands: list[str] = []
    for type_name in VERTEX_TYPES:
        commands.extend(
            (
                f"CREATE VERTEX TYPE {type_name} IF NOT EXISTS",
                f"CREATE PROPERTY {type_name}.key IF NOT EXISTS STRING",
                f"CREATE PROPERTY {type_name}.active IF NOT EXISTS BOOLEAN",
                f"CREATE PROPERTY {type_name}.last_seen IF NOT EXISTS STRING",
                f"CREATE INDEX IF NOT EXISTS ON {type_name} (key) UNIQUE",
            )
        )

    commands.extend(
        (
            "CREATE PROPERTY HAConfigEntry.entry_id IF NOT EXISTS STRING",
            "CREATE PROPERTY HAConfigEntry.domain IF NOT EXISTS STRING",
            "CREATE INDEX IF NOT EXISTS ON HAConfigEntry (entry_id) UNIQUE",
            "CREATE INDEX IF NOT EXISTS ON HAConfigEntry (domain) NOTUNIQUE",
            "CREATE PROPERTY HADomain.domain IF NOT EXISTS STRING",
            "CREATE INDEX IF NOT EXISTS ON HADomain (domain) UNIQUE",
            "CREATE PROPERTY HAArea.area_id IF NOT EXISTS STRING",
            "CREATE INDEX IF NOT EXISTS ON HAArea (area_id) UNIQUE",
            "CREATE PROPERTY HADevice.device_id IF NOT EXISTS STRING",
            "CREATE PROPERTY HADevice.area_id IF NOT EXISTS STRING",
            "CREATE INDEX IF NOT EXISTS ON HADevice (device_id) UNIQUE",
            "CREATE INDEX IF NOT EXISTS ON HADevice (area_id) NOTUNIQUE",
            "CREATE PROPERTY HAEntity.entity_id IF NOT EXISTS STRING",
            "CREATE PROPERTY HAEntity.domain IF NOT EXISTS STRING",
            "CREATE PROPERTY HAEntity.device_id IF NOT EXISTS STRING",
            "CREATE PROPERTY HAEntity.area_id IF NOT EXISTS STRING",
            "CREATE INDEX IF NOT EXISTS ON HAEntity (entity_id) UNIQUE",
            "CREATE INDEX IF NOT EXISTS ON HAEntity (domain) NOTUNIQUE",
            "CREATE INDEX IF NOT EXISTS ON HAEntity (device_id) NOTUNIQUE",
        )
    )

    for type_name in EDGE_TYPES:
        commands.append(f"CREATE EDGE TYPE {type_name} IF NOT EXISTS")

    return tuple(commands)


def snapshot_sync_commands(
    snapshot: GraphSnapshot,
    *,
    synced_at: datetime,
) -> tuple[str, ...]:
    """Return commands that replace managed edges and upsert current vertices."""
    timestamp = synced_at.isoformat()
    commands = [f"DELETE FROM {edge_type}" for edge_type in EDGE_TYPES]
    commands.extend(
        f"UPDATE {type_name} SET active = false" for type_name in VERTEX_TYPES
    )
    commands.extend(
        upsert_vertex_command(vertex, synced_at=timestamp)
        for vertex in _dedupe_vertices(snapshot.vertices)
    )
    commands.extend(create_edge_command(edge) for edge in _dedupe_edges(snapshot.edges))
    return tuple(commands)


def upsert_vertex_command(vertex: GraphVertex, *, synced_at: str) -> str:
    """Return an ArcadeDB SQL command that upserts one vertex."""
    _validate_identifier(vertex.type_name)
    properties = _json_ready(vertex.properties)
    properties["key"] = vertex.key
    properties["active"] = True
    properties["last_seen"] = synced_at
    body = json.dumps(properties, separators=(",", ":"), sort_keys=True)
    return (
        f"UPDATE {vertex.type_name} MERGE {body} "
        f"UPSERT WHERE key = {_sql_string(vertex.key)}"
    )


def create_edge_command(edge: GraphEdge) -> str:
    """Return an ArcadeDB SQL command that creates one idempotent edge."""
    for identifier in (edge.type_name, edge.from_type, edge.to_type):
        _validate_identifier(identifier)

    command = (
        f"CREATE EDGE {edge.type_name} "
        f"FROM (SELECT FROM {edge.from_type} WHERE key = {_sql_string(edge.from_key)}) "
        f"TO (SELECT FROM {edge.to_type} WHERE key = {_sql_string(edge.to_key)}) "
        "IF NOT EXISTS"
    )
    if edge.properties:
        properties = json.dumps(
            _json_ready(edge.properties), separators=(",", ":"), sort_keys=True
        )
        command = f"{command} CONTENT {properties}"
    return command


def domain_vertex(domain: str) -> GraphVertex:
    """Create a domain vertex."""
    return GraphVertex(
        VERTEX_DOMAIN,
        f"domain:{domain}",
        {"kind": "domain", "domain": domain},
    )


def _dedupe_vertices(vertices: Iterable[GraphVertex]) -> list[GraphVertex]:
    deduped: dict[tuple[str, str], GraphVertex] = {}
    for vertex in vertices:
        deduped[(vertex.type_name, vertex.key)] = vertex
    return list(deduped.values())


def _dedupe_edges(edges: Iterable[GraphEdge]) -> list[GraphEdge]:
    deduped: dict[tuple[str, str, str, str, str], GraphEdge] = {}
    for edge in edges:
        deduped[
            (edge.type_name, edge.from_type, edge.from_key, edge.to_type, edge.to_key)
        ] = edge
    return list(deduped.values())


def _json_ready(value: Any) -> Any:
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, set | frozenset):
        return [_json_ready(item) for item in sorted(value, key=str)]
    if isinstance(value, tuple | list):
        return [_json_ready(item) for item in value]
    return str(value)


def _sql_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


def _validate_identifier(value: str) -> None:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"Invalid ArcadeDB identifier: {value}")
