from __future__ import annotations

from dataclasses import dataclass

import pytest

from custom_components.arcadedb.client import (
    ArcadeDBPermanentError,
    ArcadeDBTransientError,
)
from custom_components.arcadedb.exporter import ArcadeDBExporter


@dataclass
class FakeState:
    entity_id: str
    state: str


class FakeWriter:
    def __init__(self, failures: int = 0, permanent: bool = False) -> None:
        self.failures = failures
        self.permanent = permanent
        self.calls: list[list[str]] = []

    async def write(self, lines: list[str]) -> None:
        self.calls.append(lines)
        if self.permanent:
            raise ArcadeDBPermanentError("bad request")
        if self.failures:
            self.failures -= 1
            raise ArcadeDBTransientError("temporary")


def to_line(state: FakeState, _time_fired) -> str | None:
    if state.state == "skip":
        return None
    return f"metric,entity_id={state.entity_id.replace('.', '_')} value=1.0 1"


@pytest.mark.asyncio
async def test_batches_until_batch_size() -> None:
    writer = FakeWriter()
    exporter = ArcadeDBExporter(
        writer.write,
        to_line,
        entity_filter=lambda _entity_id: True,
        batch_size=2,
        flush_interval=10,
        max_retries=0,
        retry_interval=0,
        queue_max_size=10,
    )

    await exporter.async_start()
    exporter.queue_state(FakeState("sensor.a", "1"))
    exporter.queue_state(FakeState("sensor.b", "1"))
    await exporter.async_stop()

    assert len(writer.calls) == 1
    assert len(writer.calls[0]) == 2
    assert exporter.written == 2


@pytest.mark.asyncio
async def test_retries_transient_failures() -> None:
    writer = FakeWriter(failures=1)
    exporter = ArcadeDBExporter(
        writer.write,
        to_line,
        entity_filter=lambda _entity_id: True,
        batch_size=1,
        flush_interval=0.1,
        max_retries=2,
        retry_interval=0,
        queue_max_size=10,
    )

    await exporter.async_start()
    exporter.queue_state(FakeState("sensor.a", "1"))
    await exporter.async_stop()

    assert len(writer.calls) == 2
    assert exporter.written == 1
    assert exporter.failed == 0


@pytest.mark.asyncio
async def test_drops_after_retry_exhaustion() -> None:
    writer = FakeWriter(failures=2)
    exporter = ArcadeDBExporter(
        writer.write,
        to_line,
        entity_filter=lambda _entity_id: True,
        batch_size=1,
        flush_interval=0.1,
        max_retries=1,
        retry_interval=0,
        queue_max_size=10,
    )

    await exporter.async_start()
    exporter.queue_state(FakeState("sensor.a", "1"))
    await exporter.async_stop()

    assert len(writer.calls) == 2
    assert exporter.written == 0
    assert exporter.failed == 1


@pytest.mark.asyncio
async def test_permanent_error_is_not_retried() -> None:
    writer = FakeWriter(permanent=True)
    exporter = ArcadeDBExporter(
        writer.write,
        to_line,
        entity_filter=lambda _entity_id: True,
        batch_size=1,
        flush_interval=0.1,
        max_retries=3,
        retry_interval=0,
        queue_max_size=10,
    )

    await exporter.async_start()
    exporter.queue_state(FakeState("sensor.a", "1"))
    await exporter.async_stop()

    assert len(writer.calls) == 1
    assert exporter.failed == 1


@pytest.mark.asyncio
async def test_filter_and_conversion_skips() -> None:
    writer = FakeWriter()
    exporter = ArcadeDBExporter(
        writer.write,
        to_line,
        entity_filter=lambda entity_id: entity_id == "sensor.a",
        batch_size=10,
        flush_interval=0.01,
        max_retries=0,
        retry_interval=0,
        queue_max_size=10,
    )

    await exporter.async_start()
    exporter.queue_state(FakeState("sensor.a", "skip"))
    exporter.queue_state(FakeState("sensor.b", "1"))
    await exporter.async_stop()

    assert writer.calls == []
    assert exporter.queued == 1
    assert exporter.dropped_conversion == 1

