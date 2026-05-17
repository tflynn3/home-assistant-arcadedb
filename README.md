# ArcadeDB for Home Assistant

Experimental alpha Home Assistant custom integration for exporting state changes
to ArcadeDB time series using ArcadeDB's line-protocol endpoint:

```text
POST /api/v1/ts/{database}/write?precision=ns
```

This integration is time-series export only. It does not replace Home Assistant
Recorder, and it does not replace an existing InfluxDB integration. Run it as a
parallel exporter until you have validated the data model and operational
behavior for your own installation.

## Status

- Alpha quality.
- HACS custom repository compatible.
- Tested against ArcadeDB's `/api/v1/ts/{database}/write` endpoint.
- Uses Home Assistant's built-in InfluxDB integration as the behavioral pattern:
  state-change subscription, include/exclude filters, measurement naming,
  line-protocol serialization, batching, retry, and non-blocking writes.

## Installation

### HACS custom repository

1. In HACS, add this repository as a custom repository.
2. Category: `Integration`.
3. Install `ArcadeDB`.
4. Restart Home Assistant.
5. Add the integration from Settings, or configure it in YAML.

### Manual

Copy `custom_components/arcadedb` into your Home Assistant
`custom_components` directory and restart Home Assistant.

## Configuration

The config flow supports the connection and basic batching settings.

For alpha testing, YAML is the most complete configuration surface because it
also supports include/exclude filters, default tags, and measurement controls.

ArcadeDB requires the target time-series type to exist before line-protocol
writes are accepted, unless the server is configured with
`arcadedb.tsAutoCreateType=true`. For the safest first run, use one stable
measurement name with a narrow include list and pre-create a type that matches
the emitted fields.

Example ArcadeDB schema for numeric-only state export:

```sql
CREATE TIMESERIES TYPE HomeAssistantState IF NOT EXISTS
  TIMESTAMP ts PRECISION NANOSECOND
  TAGS (domain STRING, entity_id STRING, source STRING)
  FIELDS (value DOUBLE)
```

```yaml
arcadedb:
  url: http://arcadedb.example.local:2480
  database: homeassistant
  username: !secret arcadedb_username
  password: !secret arcadedb_password
  precision: ns
  batch_size: 100
  flush_interval: 5
  max_retries: 3
  retry_interval: 20
  verify_ssl: true
  include:
    entities:
      - sensor.example_temperature
    domains:
      - sensor
    entity_globs:
      - sensor.energy_*
  exclude:
    entities:
      - sensor.noisy_example
  default_tags:
    source: homeassistant
  override_measurement: HomeAssistantState
  ignore_attributes:
    - attribution
    - device_class
    - friendly_name
    - state_class
    - unit_of_measurement
```

Supported measurement rules:

- `measurement_attr: unit_of_measurement` (default)
- `measurement_attr: domain__device_class`
- `measurement_attr: entity_id`
- `default_measurement`
- `override_measurement`
- per-entity/domain/glob `component_config` overrides

Example per-entity override:

```yaml
arcadedb:
  url: http://arcadedb.example.local:2480
  database: homeassistant
  username: !secret arcadedb_username
  password: !secret arcadedb_password
  include:
    entities:
      - sensor.example_temperature
  component_config:
    sensor.example_temperature:
      override_measurement: TemperatureReading
      ignore_attributes:
        - friendly_name
```

## Data Model

Each Home Assistant state change becomes one InfluxDB-style line-protocol record.

- Measurements come from the configured measurement rule and must map to an
  ArcadeDB time-series type.
- Tags include `domain` and `entity_id` by default.
- Numeric states are written as the `value` field.
- Non-numeric states are written as the `state` string field.
- `unknown`, `unavailable`, and empty states are skipped.
- Numeric attributes become fields.
- Non-numeric attributes are written as `*_str` fields.

Example output:

```text
degC,domain=sensor,entity_id=example_temperature value=21.5 1770000000000000000
```

## Rollback

1. Remove the ArcadeDB integration entry from Home Assistant.
2. Remove this repository from HACS, or delete `custom_components/arcadedb`.
3. Restart Home Assistant if HACS or Home Assistant asks for it.
4. Existing Recorder and InfluxDB configuration can remain unchanged.

## Future Graph Support

Future versions may add an optional graph mode that writes Home Assistant
metadata and relationships into ArcadeDB:

- entity to device
- device to area
- automation to entity dependencies
- integration and domain relationships

Graph support should remain separate from the time-series exporter and should
not be required for basic state export.

## Development

```bash
uv sync --dev
uv run ruff check .
uv run pytest
```
