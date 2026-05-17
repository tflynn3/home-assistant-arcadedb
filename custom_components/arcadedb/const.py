"""Constants for the ArcadeDB custom integration."""

from __future__ import annotations

DOMAIN = "arcadedb"

CONF_BATCH_SIZE = "batch_size"
CONF_DATABASE = "database"
CONF_DEFAULT_MEASUREMENT = "default_measurement"
CONF_DEFAULT_TAGS = "default_tags"
CONF_ENTITY_GLOBS = "entity_globs"
CONF_FLUSH_INTERVAL = "flush_interval"
CONF_GRAPH = "graph"
CONF_GRAPH_ENABLED = "enabled"
CONF_GRAPH_INCLUDE_STATE_SNAPSHOT = "include_state_snapshot"
CONF_GRAPH_SYNC_INTERVAL = "sync_interval"
CONF_FULL_ENTITY_ID_TAG = "full_entity_id_tag"
CONF_IGNORE_ATTRIBUTES = "ignore_attributes"
CONF_INCLUDE_ATTRIBUTES = "include_attributes"
CONF_MEASUREMENT_ATTR = "measurement_attr"
CONF_OVERRIDE_MEASUREMENT = "override_measurement"
CONF_PRECISION = "precision"
CONF_QUEUE_MAX_SIZE = "queue_max_size"
CONF_RETRY_COUNT = "max_retries"
CONF_RETRY_INTERVAL = "retry_interval"
CONF_STATE_TYPE_FIELD = "state_type_field"
CONF_TAGS_ATTRIBUTES = "tags_attributes"
CONF_COMPONENT_CONFIG = "component_config"
CONF_COMPONENT_CONFIG_DOMAIN = "component_config_domain"
CONF_COMPONENT_CONFIG_GLOB = "component_config_glob"

DEFAULT_BATCH_SIZE = 100
DEFAULT_DATABASE = "homeassistant"
DEFAULT_FLUSH_INTERVAL = 5.0
DEFAULT_GRAPH_SYNC_INTERVAL = 3600.0
DEFAULT_MEASUREMENT_ATTR = "unit_of_measurement"
DEFAULT_PRECISION = "ns"
DEFAULT_QUEUE_MAX_SIZE = 10000
DEFAULT_RETRY_COUNT = 3
DEFAULT_RETRY_INTERVAL = 20.0

MEASUREMENT_ATTR_OPTIONS = ("unit_of_measurement", "domain__device_class", "entity_id")
PRECISION_OPTIONS = ("ns", "us", "ms", "s")

EVENT_NEW_STATE = "new_state"
