from __future__ import annotations

from custom_components.arcadedb.filtering import entity_filter_from_config


def test_allows_all_when_no_include_or_exclude() -> None:
    entity_filter = entity_filter_from_config({})

    assert entity_filter("sensor.temperature")
    assert entity_filter("light.kitchen")


def test_include_entities_domains_and_globs() -> None:
    entity_filter = entity_filter_from_config(
        {
            "include": {
                "entities": ["sensor.temperature"],
                "domains": ["light"],
                "entity_globs": ["binary_sensor.door_*"],
            }
        }
    )

    assert entity_filter("sensor.temperature")
    assert entity_filter("light.kitchen")
    assert entity_filter("binary_sensor.door_back")
    assert not entity_filter("switch.fan")


def test_exclude_wins_over_include() -> None:
    entity_filter = entity_filter_from_config(
        {
            "include": {"domains": ["sensor"]},
            "exclude": {
                "entities": ["sensor.noisy"],
                "entity_globs": ["sensor.debug_*"],
            },
        }
    )

    assert entity_filter("sensor.temperature")
    assert not entity_filter("sensor.noisy")
    assert not entity_filter("sensor.debug_counter")

