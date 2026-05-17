from __future__ import annotations

from custom_components.arcadedb.client import ArcadeDBTransientError
from custom_components.arcadedb.graph_exporter import (
    ArcadeDBGraphExporter,
    GraphExporterConfig,
)
from custom_components.arcadedb.graph_model import GraphSnapshot, GraphVertex


class FakeCommandClient:
    def __init__(self, failures: int = 0) -> None:
        self.failures = failures
        self.calls: list[tuple[str, str]] = []

    async def command(self, language: str, command: str) -> str:
        self.calls.append((language, command))
        if self.failures:
            self.failures -= 1
            raise ArcadeDBTransientError("temporary")
        return "{}"


async def _snapshot() -> GraphSnapshot:
    return GraphSnapshot(
        vertices=(
            GraphVertex(
                "HAEntity",
                "entity:sensor.temperature",
                {"entity_id": "sensor.temperature"},
            ),
        ),
        edges=(),
    )


def _config() -> GraphExporterConfig:
    return GraphExporterConfig(sync_interval=3600, max_retries=1, retry_interval=0)


async def test_sync_once_creates_schema_and_writes_snapshot() -> None:
    client = FakeCommandClient()
    exporter = ArcadeDBGraphExporter(client.command, _snapshot, _config())

    stats = await exporter.async_sync_once()

    assert stats.vertices == 1
    assert stats.edges == 0
    assert client.calls[0] == ("sql", "CREATE VERTEX TYPE HAConfigEntry IF NOT EXISTS")
    assert any(
        command.startswith("UPDATE HAEntity MERGE")
        for _language, command in client.calls
    )


async def test_sync_once_retries_transient_command_failure() -> None:
    client = FakeCommandClient(failures=1)
    exporter = ArcadeDBGraphExporter(client.command, _snapshot, _config())

    stats = await exporter.async_sync_once()

    assert stats.vertices == 1
    assert client.calls.count(
        ("sql", "CREATE VERTEX TYPE HAConfigEntry IF NOT EXISTS")
    ) == 2
