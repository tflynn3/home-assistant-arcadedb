"""Home Assistant state to ArcadeDB line protocol conversion."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .const import DEFAULT_MEASUREMENT_ATTR, DEFAULT_PRECISION

STATE_UNAVAILABLE = "unavailable"
STATE_UNKNOWN = "unknown"

_RE_DIGIT_TAIL = re.compile(r"^[^\.]*\d+\.?\d+[^\.]*$")
_RE_DECIMAL = re.compile(r"[^\d.]+")


@dataclass(frozen=True)
class LineProtocolConfig:
    """Settings that control Home Assistant state serialization."""

    precision: str = DEFAULT_PRECISION
    measurement_attr: str = DEFAULT_MEASUREMENT_ATTR
    default_measurement: str | None = None
    override_measurement: str | None = None
    default_tags: Mapping[str, Any] = field(default_factory=dict)
    tags_attributes: tuple[str, ...] = ()
    full_entity_id_tag: str | None = None
    state_type_field: str | None = None
    include_attributes: bool = True
    ignore_attributes: tuple[str, ...] = ()
    component_config: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    component_config_domain: Mapping[str, Mapping[str, Any]] = field(
        default_factory=dict
    )
    component_config_glob: Mapping[str, Mapping[str, Any]] = field(
        default_factory=dict
    )


def state_to_line(
    state: Any,
    config: LineProtocolConfig,
    time_fired: datetime | None = None,
) -> str | None:
    """Convert a Home Assistant state object into one line protocol record."""
    entity_id = str(getattr(state, "entity_id", ""))
    if "." not in entity_id:
        return None

    raw_state = getattr(state, "state", None)
    if raw_state is None:
        return None

    state_value = str(raw_state)
    if state_value in (STATE_UNKNOWN, STATE_UNAVAILABLE, ""):
        return None

    attributes = dict(getattr(state, "attributes", {}) or {})
    domain, object_id = entity_id.split(".", 1)
    entity_config = _entity_config(entity_id, domain, config)
    measurement, include_uom, include_device_class = _measurement_name(
        entity_id, domain, attributes, config, entity_config
    )

    fields, state_type = _state_fields(state_value)
    state_type_field = entity_config.get(
        "state_type_field", config.state_type_field
    )
    if state_type_field:
        fields[str(state_type_field)] = state_type
    include_attributes = entity_config.get(
        "include_attributes", config.include_attributes
    )
    ignore_attributes = set(config.ignore_attributes)
    ignore_attributes.update(entity_config.get("ignore_attributes", ()) or ())

    tag_attribute_names = set(config.tags_attributes)
    if include_attributes:
        for key, value in attributes.items():
            if key in tag_attribute_names:
                continue
            if key in ignore_attributes:
                continue
            if key == "unit_of_measurement" and not include_uom:
                continue
            if key == "device_class" and not include_device_class:
                continue

            field_key = key if key not in fields else f"{key}_"
            fields.update(_attribute_fields(field_key, value))

    if not fields:
        return None

    tags: dict[str, Any] = {"domain": domain, "entity_id": object_id}
    full_entity_id_tag = entity_config.get(
        "full_entity_id_tag", config.full_entity_id_tag
    )
    if full_entity_id_tag:
        tags[str(full_entity_id_tag)] = entity_id
    for key in config.tags_attributes:
        if key in attributes:
            tags[key] = attributes[key]
    tags.update(config.default_tags)

    timestamp_source = time_fired or getattr(state, "last_updated", None)
    timestamp = _timestamp_for_precision(timestamp_source, config.precision)

    measurement_part = _escape_measurement(str(measurement))
    tag_part = _format_tags(tags)
    field_part = _format_fields(fields)

    if tag_part:
        measurement_part = f"{measurement_part},{tag_part}"

    return f"{measurement_part} {field_part} {timestamp}"


def _entity_config(
    entity_id: str,
    domain: str,
    config: LineProtocolConfig,
) -> Mapping[str, Any]:
    if entity_id in config.component_config:
        return config.component_config[entity_id]
    if domain in config.component_config_domain:
        return config.component_config_domain[domain]
    for entity_glob, value in config.component_config_glob.items():
        if re.fullmatch(_glob_to_regex(entity_glob), entity_id):
            return value
    return {}


def _glob_to_regex(pattern: str) -> str:
    return re.escape(pattern).replace(r"\*", ".*").replace(r"\?", ".")


def _measurement_name(
    entity_id: str,
    domain: str,
    attributes: Mapping[str, Any],
    config: LineProtocolConfig,
    entity_config: Mapping[str, Any],
) -> tuple[str, bool, bool]:
    include_uom = True
    include_device_class = True

    measurement = entity_config.get("override_measurement")
    if measurement:
        return str(measurement), include_uom, include_device_class

    if config.override_measurement:
        return config.override_measurement, include_uom, include_device_class

    if config.measurement_attr == "entity_id":
        measurement = entity_id
    elif config.measurement_attr == "domain__device_class":
        device_class = attributes.get("device_class")
        if device_class in (None, ""):
            measurement = domain
        else:
            measurement = f"{domain}__{device_class}"
            include_device_class = False
    else:
        measurement = attributes.get(config.measurement_attr)
        if measurement not in (None, ""):
            include_uom = config.measurement_attr != "unit_of_measurement"

    if measurement in (None, ""):
        measurement = config.default_measurement or entity_id

    return str(measurement), include_uom, include_device_class


def _state_fields(value: str) -> tuple[dict[str, Any], str]:
    number = _finite_float(value)
    if number is not None:
        return {"value": number}, "number"
    return {"state": value}, "string"


def _attribute_fields(key: str, value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, bool):
        return {key: value}
    if isinstance(value, int):
        return {key: value}
    if isinstance(value, float):
        return {key: value} if math.isfinite(value) else {}

    number = _finite_float(str(value))
    if number is not None:
        return {key: number}

    text = str(value)
    fields: dict[str, Any] = {f"{key}_str": text}
    if _RE_DIGIT_TAIL.match(text):
        stripped = _RE_DECIMAL.sub("", text)
        number = _finite_float(stripped)
        if number is not None:
            fields[key] = number
    return fields


def _finite_float(value: str) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _timestamp_for_precision(value: datetime | None, precision: str) -> int:
    dt = value or datetime.now(UTC)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    seconds = dt.timestamp()
    if precision == "s":
        return int(seconds)
    if precision == "ms":
        return int(seconds * 1_000)
    if precision == "us":
        return int(seconds * 1_000_000)
    return int(seconds * 1_000_000_000)


def _format_tags(tags: Mapping[str, Any]) -> str:
    parts = []
    for key, value in sorted(tags.items()):
        if value in (None, ""):
            continue
        parts.append(f"{_escape_tag(str(key))}={_escape_tag(str(value))}")
    return ",".join(parts)


def _format_fields(fields: Mapping[str, Any]) -> str:
    return ",".join(
        f"{_escape_field_key(str(key))}={_format_field_value(value)}"
        for key, value in sorted(fields.items())
    )


def _format_field_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int) and not isinstance(value, bool):
        return f"{value}i"
    if isinstance(value, float):
        return repr(value)
    return f'"{_escape_field_string(str(value))}"'


def _escape_measurement(value: str) -> str:
    return _escape(value, {" ", ","})


def _escape_tag(value: str) -> str:
    return _escape(value, {" ", ",", "="})


def _escape_field_key(value: str) -> str:
    return _escape(value, {" ", ",", "="})


def _escape_field_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _escape(value: str, escaped_chars: set[str]) -> str:
    return "".join(f"\\{char}" if char in escaped_chars else char for char in value)
