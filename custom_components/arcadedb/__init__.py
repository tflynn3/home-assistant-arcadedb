"""ArcadeDB time-series exporter for Home Assistant."""

from __future__ import annotations

from typing import Any

try:
    from .ha_runtime import (
        CONFIG_SCHEMA,
        async_setup,
        async_setup_entry,
        async_unload_entry,
    )
except ModuleNotFoundError as err:
    if err.name != "homeassistant":
        raise

    CONFIG_SCHEMA = None

    async def async_setup(*_args: Any, **_kwargs: Any) -> bool:
        """Placeholder used only when Home Assistant is not installed."""
        raise RuntimeError("Home Assistant is required to set up this integration")

    async def async_setup_entry(*_args: Any, **_kwargs: Any) -> bool:
        """Placeholder used only when Home Assistant is not installed."""
        raise RuntimeError("Home Assistant is required to set up this integration")

    async def async_unload_entry(*_args: Any, **_kwargs: Any) -> bool:
        """Placeholder used only when Home Assistant is not installed."""
        raise RuntimeError("Home Assistant is required to unload this integration")

