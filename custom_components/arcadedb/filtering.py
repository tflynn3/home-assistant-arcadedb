"""Include/exclude filtering for ArcadeDB exports."""

from __future__ import annotations

from collections.abc import Mapping
from fnmatch import fnmatchcase
from typing import Any


def entity_filter_from_config(config: Mapping[str, Any]):
    """Return an entity predicate from Home Assistant-style include/exclude config."""
    include = _filter_spec(config.get("include"))
    exclude = _filter_spec(config.get("exclude"))

    def entity_filter(entity_id: str) -> bool:
        domain = entity_id.split(".", 1)[0]
        if _matches(exclude, entity_id, domain):
            return False
        if _is_empty(include):
            return True
        return _matches(include, entity_id, domain)

    return entity_filter


def _filter_spec(value: Any) -> dict[str, tuple[str, ...]]:
    if not isinstance(value, Mapping):
        return {"entities": (), "domains": (), "entity_globs": ()}

    return {
        "entities": tuple(str(item) for item in value.get("entities", ()) or ()),
        "domains": tuple(str(item) for item in value.get("domains", ()) or ()),
        "entity_globs": tuple(
            str(item) for item in value.get("entity_globs", ()) or ()
        ),
    }


def _is_empty(spec: Mapping[str, tuple[str, ...]]) -> bool:
    return not spec["entities"] and not spec["domains"] and not spec["entity_globs"]


def _matches(spec: Mapping[str, tuple[str, ...]], entity_id: str, domain: str) -> bool:
    return (
        entity_id in spec["entities"]
        or domain in spec["domains"]
        or any(fnmatchcase(entity_id, glob) for glob in spec["entity_globs"])
    )

