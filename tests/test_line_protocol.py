from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from custom_components.arcadedb.line_protocol import (
    LineProtocolConfig,
    state_to_line,
)


@dataclass
class FakeState:
    entity_id: str
    state: str
    attributes: dict[str, Any]


TS = datetime(2026, 1, 2, 3, 4, 5, 123456, tzinfo=UTC)


def test_numeric_state_uses_value_field_and_default_tags() -> None:
    line = state_to_line(
        FakeState(
            "sensor.office_temperature",
            "21.5",
            {"unit_of_measurement": "degC", "friendly_name": "Office Temp"},
        ),
        LineProtocolConfig(default_tags={"source": "ha"}),
        TS,
    )

    assert line is not None
    assert line.startswith("degC,domain=sensor,entity_id=office_temperature,source=ha ")
    assert "value=21.5" in line
    assert "friendly_name_str=\"Office Temp\"" in line
    assert line.endswith(" 1767323045123456000")


def test_string_state_uses_state_field() -> None:
    line = state_to_line(
        FakeState("binary_sensor.door", "on", {"device_class": "door"}),
        LineProtocolConfig(measurement_attr="domain__device_class"),
        TS,
    )

    assert line is not None
    assert line.startswith("binary_sensor__door,")
    assert 'state="on"' in line
    assert "device_class" not in line


def test_unknown_and_unavailable_states_are_skipped() -> None:
    config = LineProtocolConfig()

    assert state_to_line(FakeState("sensor.test", "unknown", {}), config, TS) is None
    assert (
        state_to_line(FakeState("sensor.test", "unavailable", {}), config, TS)
        is None
    )
    assert state_to_line(FakeState("sensor.test", "", {}), config, TS) is None


def test_attribute_numeric_and_string_values() -> None:
    line = state_to_line(
        FakeState(
            "sensor.power",
            "100",
            {"unit_of_measurement": "W", "battery": "95%", "mode": "auto"},
        ),
        LineProtocolConfig(),
        TS,
    )

    assert line is not None
    assert "battery=95.0" in line
    assert 'battery_str="95%"' in line
    assert 'mode_str="auto"' in line
    assert "value=100.0" in line


def test_component_measurement_and_ignore_override() -> None:
    line = state_to_line(
        FakeState(
            "sensor.power",
            "100",
            {"unit_of_measurement": "W", "friendly_name": "Power"},
        ),
        LineProtocolConfig(
            component_config={
                "sensor.power": {
                    "override_measurement": "PowerReading",
                    "ignore_attributes": ["friendly_name"],
                }
            }
        ),
        TS,
    )

    assert line is not None
    assert line.startswith("PowerReading,")
    assert "friendly_name" not in line


def test_precision_ms_timestamp() -> None:
    line = state_to_line(
        FakeState("sensor.test", "1", {"unit_of_measurement": "items"}),
        LineProtocolConfig(precision="ms"),
        TS,
    )

    assert line is not None
    assert line.endswith(" 1767323045123")
