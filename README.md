# ArcadeDB for Home Assistant

Experimental alpha Home Assistant custom integration for exporting state changes
and an optional Home Assistant metadata graph to ArcadeDB.

Time-series writes use ArcadeDB's line-protocol endpoint:

```text
POST /api/v1/ts/{database}/write?precision=ns
```

This integration does not replace Home Assistant Recorder, and it does not
replace an existing InfluxDB integration. Run it as a parallel exporter until
you have validated the data model and operational behavior for your own
installation.

## Status

- Alpha quality.
- HACS custom repository compatible.
- Tested against ArcadeDB's `/api/v1/ts/{database}/write` endpoint.
- Uses Home Assistant's built-in InfluxDB integration as the behavioral pattern:
  state-change subscription, include/exclude filters, measurement naming,
  line-protocol serialization, batching, retry, and non-blocking writes.
- Optional graph mode mirrors Home Assistant's registry model into ArcadeDB:
  config entries, domains, areas, devices, entities, and their relationships.

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
also supports include/exclude filters, default tags, measurement controls, and
graph mode.

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

Example ArcadeDB schema for wide mixed state export into one stable
measurement:

```sql
CREATE TIMESERIES TYPE HomeAssistantEvent IF NOT EXISTS
  TIMESTAMP ts PRECISION NANOSECOND
  TAGS (domain STRING, entity_id STRING, source STRING)
  FIELDS (value DOUBLE, state STRING)
```

Example ArcadeDB schema for enriched wide export that is easier to join to the
graph:

```sql
CREATE TIMESERIES TYPE HomeAssistantStateEnriched IF NOT EXISTS
  TIMESTAMP ts PRECISION NANOSECOND
  TAGS (
    domain STRING,
    entity_id STRING,
    full_entity_id STRING,
    friendly_name STRING,
    unit_of_measurement STRING,
    device_class STRING,
    state_class STRING,
    source STRING
  )
  FIELDS (value DOUBLE, state STRING, state_type STRING)
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
  include_attributes: false
  ignore_attributes:
    - attribution
    - device_class
    - friendly_name
    - state_class
    - unit_of_measurement
```

Wide export example:

```yaml
arcadedb:
  url: http://arcadedb.example.local:2480
  database: homeassistant
  username: !secret arcadedb_username
  password: !secret arcadedb_password
  precision: ns
  batch_size: 500
  flush_interval: 5
  max_retries: 3
  retry_interval: 20
  queue_max_size: 50000
  verify_ssl: true
  default_tags:
    source: homeassistant
  override_measurement: HomeAssistantStateEnriched
  full_entity_id_tag: full_entity_id
  state_type_field: state_type
  tags_attributes:
    - friendly_name
    - unit_of_measurement
    - device_class
    - state_class
  include_attributes: false
```

With no `include` or `exclude` filters, all state changes are eligible for
export. `include_attributes: false` keeps the emitted line protocol compatible
with a fixed wide type. The enriched example adds:

- `full_entity_id`, which matches `HAEntity.entity_id` in graph mode
- selected Home Assistant metadata tags for label, unit, device class, and state
  class
- `state_type`, which is `number` for `value` rows and `string` for `state`
  rows

Supported measurement rules:

- `measurement_attr: unit_of_measurement` (default)
- `measurement_attr: domain__device_class`
- `measurement_attr: entity_id`
- `default_measurement`
- `override_measurement`
- `include_attributes: false` to suppress attributes for fixed wide schemas
- `tags_attributes` to copy selected Home Assistant state attributes into tags
- `full_entity_id_tag` to add a generated tag that matches graph
  `HAEntity.entity_id`
- `state_type_field` to add a generated string field describing whether the
  exported state was numeric or string
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
      include_attributes: false
      ignore_attributes:
        - friendly_name
```

## Graph Mode

Graph mode is optional and configured through YAML in this alpha release.

```yaml
arcadedb:
  url: http://arcadedb.example.local:2480
  database: homeassistant
  username: !secret arcadedb_username
  password: !secret arcadedb_password
  graph:
    enabled: true
    sync_interval: 300
    include_state_snapshot: true
```

The integration creates and maintains these vertex types:

- `HAConfigEntry`
- `HADomain`
- `HAArea`
- `HADevice`
- `HAEntity`

It creates these edge types:

- `HA_CONFIG_ENTRY_DOMAIN`
- `HA_DEVICE_CONFIG_ENTRY`
- `HA_ENTITY_CONFIG_ENTRY`
- `HA_DEVICE_AREA`
- `HA_ENTITY_AREA`
- `HA_ENTITY_DEVICE`
- `HA_ENTITY_DOMAIN`
- `HA_DEVICE_VIA_DEVICE`

Each graph sync:

- creates missing schema objects
- marks managed vertices inactive, then upserts current vertices as active
- replaces managed edges so moved devices/entities do not keep stale
  relationships
- optionally stores a current state snapshot on entity vertices

This is intended for AI-agent query workloads where the agent needs to answer
questions such as "which entities belong to this device?", "what area is this
device in?", or "which integration owns this entity?" while time-series data
stays in ArcadeDB time-series types.

## Data Model

Each Home Assistant state change becomes one InfluxDB-style line-protocol record.

- Measurements come from the configured measurement rule and must map to an
  ArcadeDB time-series type.
- Tags include `domain` and `entity_id` by default.
- `entity_id` is the Home Assistant object id without the domain prefix.
- `full_entity_id_tag` can add a tag such as `full_entity_id=sensor.example`
  for graph joins.
- `tags_attributes` can promote selected state attributes such as
  `friendly_name`, `unit_of_measurement`, `device_class`, and `state_class` into
  tags.
- Numeric states are written as the `value` field.
- Non-numeric states are written as the `state` string field.
- `state_type_field` can add `state_type="number"` or `state_type="string"` to
  distinguish rows where ArcadeDB returns default values for absent fields.
- `unknown`, `unavailable`, and empty states are skipped.
- Numeric attributes become fields.
- Non-numeric attributes are written as `*_str` fields.
- Attributes are omitted when `include_attributes: false`.

Example output:

```text
degC,domain=sensor,entity_id=example_temperature value=21.5 1770000000000000000
```

## Rollback

1. Remove the ArcadeDB integration entry from Home Assistant.
2. Remove this repository from HACS, or delete `custom_components/arcadedb`.
3. Restart Home Assistant if HACS or Home Assistant asks for it.
4. Existing Recorder and InfluxDB configuration can remain unchanged.

## Release Guidance

For HACS users, publish a GitHub release for each version tag:

```bash
git tag v0.4.0
git push origin main v0.4.0
gh release create v0.4.0 --title v0.4.0 --notes "..."
```

## Future Graph Support

Future versions may expand graph mode with optional automation dependency
parsing, service/action metadata, labels/floors as first-class vertices, and
agent-oriented query helpers. These should remain separate from the
time-series exporter and should not be required for basic state export.

## Development

```bash
uv sync --dev
uv run ruff check .
uv run pytest
```
