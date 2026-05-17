"""Background batching and retry loop for ArcadeDB exports."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .client import ArcadeDBPermanentError, ArcadeDBTransientError

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class QueuedState:
    """State queued for conversion and export."""

    state: Any
    time_fired: datetime | None


class ArcadeDBExporter:
    """Queue, batch, and retry state exports without blocking Home Assistant."""

    def __init__(
        self,
        write_lines: Callable[[list[str]], Awaitable[None]],
        state_to_line: Callable[[Any, datetime | None], str | None],
        *,
        entity_filter: Callable[[str], bool],
        batch_size: int,
        flush_interval: float,
        max_retries: int,
        retry_interval: float,
        queue_max_size: int,
        logger: logging.Logger | None = None,
    ) -> None:
        self._write_lines = write_lines
        self._state_to_line = state_to_line
        self._entity_filter = entity_filter
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._max_retries = max_retries
        self._retry_interval = retry_interval
        self._queue: asyncio.Queue[QueuedState | None] = asyncio.Queue(queue_max_size)
        self._logger = logger or _LOGGER
        self._task: asyncio.Task[None] | None = None
        self._stop_after_batch = False
        self._consecutive_failed_lines = 0

        self.queued = 0
        self.written = 0
        self.dropped_queue_full = 0
        self.dropped_conversion = 0
        self.failed = 0

    async def async_start(self) -> None:
        """Start the background writer."""
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="arcadedb-exporter")

    async def async_stop(self) -> None:
        """Flush pending work and stop the background writer."""
        if self._task is None:
            return
        await self._queue.put(None)
        await self._task
        self._task = None

    def queue_state(self, state: Any, time_fired: datetime | None = None) -> None:
        """Queue a state for export."""
        entity_id = str(getattr(state, "entity_id", ""))
        if not self._entity_filter(entity_id):
            return

        try:
            self._queue.put_nowait(QueuedState(state, time_fired))
        except asyncio.QueueFull:
            self.dropped_queue_full += 1
            if self.dropped_queue_full == 1:
                self._logger.error("ArcadeDB export queue is full; dropping events")
            return

        self.queued += 1

    async def _run(self) -> None:
        while True:
            batch = await self._next_batch()
            if batch:
                lines = self._convert_batch(batch)
                if lines:
                    await self._write_with_retry(lines)
            if self._stop_after_batch:
                break

    async def _next_batch(self) -> list[QueuedState]:
        item = await self._queue.get()
        if item is None:
            self._stop_after_batch = True
            return []

        batch = [item]
        deadline = asyncio.get_running_loop().time() + self._flush_interval
        while len(batch) < self._batch_size:
            timeout = deadline - asyncio.get_running_loop().time()
            if timeout <= 0:
                break
            try:
                item = await asyncio.wait_for(self._queue.get(), timeout=timeout)
            except TimeoutError:
                break
            if item is None:
                self._stop_after_batch = True
                break
            batch.append(item)
        return batch

    def _convert_batch(self, batch: list[QueuedState]) -> list[str]:
        lines = []
        for item in batch:
            try:
                line = self._state_to_line(item.state, item.time_fired)
            except Exception:
                self._logger.exception("Failed to convert Home Assistant state")
                self.dropped_conversion += 1
                continue
            if line is None:
                self.dropped_conversion += 1
                continue
            lines.append(line)
        return lines

    async def _write_with_retry(self, lines: list[str]) -> None:
        for attempt in range(self._max_retries + 1):
            try:
                await self._write_lines(lines)
            except ArcadeDBTransientError as err:
                if attempt < self._max_retries:
                    await asyncio.sleep(self._retry_interval)
                    continue
                self._record_failed(lines, err)
                return
            except ArcadeDBPermanentError as err:
                self._logger.error("ArcadeDB export dropped a batch: %s", err)
                self.failed += len(lines)
                return

            if self._consecutive_failed_lines:
                self._logger.info(
                    "ArcadeDB export resumed after dropping %d line(s)",
                    self._consecutive_failed_lines,
                )
                self._consecutive_failed_lines = 0
            self.written += len(lines)
            return

    def _record_failed(self, lines: list[str], err: Exception) -> None:
        if self._consecutive_failed_lines == 0:
            self._logger.error("ArcadeDB export failed after retries: %s", err)
        self._consecutive_failed_lines += len(lines)
        self.failed += len(lines)

