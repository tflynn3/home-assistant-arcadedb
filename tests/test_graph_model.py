from __future__ import annotations

from datetime import UTC, datetime

import pytest

from custom_components.arcadedb.graph_model import (
    EDGE_ENTITY_DEVICE,
    EDGE_TYPES,
    VERTEX_DEVICE,
    VERTEX_ENTITY,
    GraphEdge,
    GraphSnapshot,
    GraphVertex,
    create_edge_command,
    graph_schema_commands,
    snapshot_sync_commands,
    upsert_vertex_command,
)

SYNCED_AT = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def test_schema_creates_vertex_edge_types_and_unique_keys() -> None:
    commands = graph_schema_commands()

    assert "CREATE VERTEX TYPE HAEntity IF NOT EXISTS" in commands
    assert "CREATE PROPERTY HAEntity.key IF NOT EXISTS STRING" in commands
    assert "CREATE INDEX IF NOT EXISTS ON HAEntity (key) UNIQUE" in commands
    assert "CREATE EDGE TYPE HA_ENTITY_DEVICE IF NOT EXISTS" in commands


def test_upsert_vertex_uses_merge_json_and_escaped_key() -> None:
    command = upsert_vertex_command(
        GraphVertex(
            VERTEX_ENTITY,
            "entity:sensor.o'reilly",
            {
                "entity_id": "sensor.o'reilly",
                "domain": "sensor",
                "name": "O'Reilly Sensor",
            },
        ),
        synced_at=SYNCED_AT.isoformat(),
    )

    assert command.startswith("UPDATE HAEntity MERGE ")
    assert '"name":"O\'Reilly Sensor"' in command
    assert "UPSERT WHERE key = 'entity:sensor.o''reilly'" in command


def test_create_edge_command_is_idempotent() -> None:
    command = create_edge_command(
        GraphEdge(
            EDGE_ENTITY_DEVICE,
            VERTEX_ENTITY,
            "entity:sensor.temperature",
            VERTEX_DEVICE,
            "device:abc",
        )
    )

    assert command == (
        "CREATE EDGE HA_ENTITY_DEVICE "
        "FROM (SELECT FROM HAEntity WHERE key = 'entity:sensor.temperature') "
        "TO (SELECT FROM HADevice WHERE key = 'device:abc') "
        "IF NOT EXISTS"
    )


def test_snapshot_sync_replaces_edges_marks_inactive_then_upserts() -> None:
    commands = snapshot_sync_commands(
        GraphSnapshot(
            vertices=(
                GraphVertex(
                    VERTEX_ENTITY,
                    "entity:sensor.temperature",
                    {"entity_id": "sensor.temperature"},
                ),
                GraphVertex(
                    VERTEX_ENTITY,
                    "entity:sensor.temperature",
                    {"entity_id": "sensor.temperature"},
                ),
            ),
            edges=(
                GraphEdge(
                    EDGE_ENTITY_DEVICE,
                    VERTEX_ENTITY,
                    "entity:sensor.temperature",
                    VERTEX_DEVICE,
                    "device:abc",
                ),
                GraphEdge(
                    EDGE_ENTITY_DEVICE,
                    VERTEX_ENTITY,
                    "entity:sensor.temperature",
                    VERTEX_DEVICE,
                    "device:abc",
                ),
            ),
        ),
        synced_at=SYNCED_AT,
    )

    assert commands[: len(EDGE_TYPES)] == tuple(
        f"DELETE FROM {edge_type}" for edge_type in EDGE_TYPES
    )
    assert commands.count(
        "CREATE EDGE HA_ENTITY_DEVICE "
        "FROM (SELECT FROM HAEntity WHERE key = 'entity:sensor.temperature') "
        "TO (SELECT FROM HADevice WHERE key = 'device:abc') "
        "IF NOT EXISTS"
    ) == 1
    assert sum(command.startswith("UPDATE HAEntity MERGE") for command in commands) == 1


def test_invalid_arcadedb_identifier_is_rejected() -> None:
    with pytest.raises(ValueError):
        upsert_vertex_command(
            GraphVertex("HAEntity; DROP TYPE HAEntity", "x", {}),
            synced_at=SYNCED_AT.isoformat(),
        )
