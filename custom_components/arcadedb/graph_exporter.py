"""Periodic Home Assistant metadata graph export to ArcadeDB."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from .client import ArcadeDBPermanentError, ArcadeDBTransientError
from .graph_model import GraphSnapshot, graph_schema_commands, snapshot_sync_commands

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class GraphExporterConfig:
    """Settings for graph snapshot export."""

    sync_interval: float
    max_retries: int
    retry_interval: float


@dataclass(frozen=True)
class GraphSyncStats:
    """Counts from the most recent graph sync."""

    vertices: int
    edges: int
    commands: int


class ArcadeDBGraphExporter:
    """Synchronize HA registries into ArcadeDB graph types."""

    def __init__(
        self,
        command: Callable[[str, str], Awaitable[str]],
        snapshot: Callable[[], Awaitable[GraphSnapshot]],
        config: GraphExporterConfig,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self._command = command
        self._snapshot = snapshot
        self._config = config
        self._logger = logger or _LOGGER
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._schema_ready = False
        self._consecutive_failures = 0

        self.last_sync: GraphSyncStats | None = None

    async def async_start(self) -> None:
        """Start the graph sync loop."""
        if self._task is None:
            self._stop.clear()
            self._task = asyncio.create_task(
                self._run(), name="arcadedb-graph-exporter"
            )

    async def async_stop(self) -> None:
        """Stop the graph sync loop."""
        if self._task is None:
            return
        self._stop.set()
        await self._task
        self._task = None

    async def async_sync_once(self) -> GraphSyncStats:
        """Run one graph sync now."""
        for attempt in range(self._config.max_retries + 1):
            try:
                return await self._sync_once()
            except ArcadeDBTransientError as err:
                if attempt < self._config.max_retries:
                    await asyncio.sleep(self._config.retry_interval)
                    continue
                self._record_failure(err)
                raise
            except ArcadeDBPermanentError as err:
                self._record_failure(err)
                raise

        raise ArcadeDBTransientError("ArcadeDB graph sync exhausted retries")

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.async_sync_once()
            except ArcadeDBPermanentError:
                await self._sleep()
            except ArcadeDBTransientError:
                await self._sleep()
            else:
                await self._sleep()

    async def _sync_once(self) -> GraphSyncStats:
        if not self._schema_ready:
            for command in graph_schema_commands():
                await self._command("sql", command)
            self._schema_ready = True

        snapshot = await self._snapshot()
        commands = snapshot_sync_commands(snapshot, synced_at=datetime.now(UTC))
        for command in commands:
            await self._command("sql", command)

        stats = GraphSyncStats(
            vertices=len(snapshot.vertices),
            edges=len(snapshot.edges),
            commands=len(commands),
        )
        self.last_sync = stats
        if self._consecutive_failures:
            self._logger.info("ArcadeDB graph sync recovered")
            self._consecutive_failures = 0
        self._logger.info(
            "ArcadeDB graph sync wrote %d vertices and %d edges",
            stats.vertices,
            stats.edges,
        )
        return stats

    async def _sleep(self) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), self._config.sync_interval)
        except TimeoutError:
            return

    def _record_failure(self, err: Exception) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures == 1:
            self._logger.error("ArcadeDB graph sync failed: %s", err)
