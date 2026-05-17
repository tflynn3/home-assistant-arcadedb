"""Async ArcadeDB HTTP client for time-series writes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import quote

from aiohttp import BasicAuth
from yarl import URL

from .const import DEFAULT_PRECISION

if TYPE_CHECKING:
    from collections.abc import Sequence

    from aiohttp import ClientSession


class ArcadeDBError(Exception):
    """Base ArcadeDB client error."""


class ArcadeDBAuthError(ArcadeDBError):
    """ArcadeDB rejected authentication."""


class ArcadeDBPermanentError(ArcadeDBError):
    """ArcadeDB rejected the request permanently."""


class ArcadeDBTransientError(ArcadeDBError):
    """ArcadeDB request failed in a way that can be retried."""


@dataclass(frozen=True)
class ArcadeDBClientConfig:
    """ArcadeDB connection settings."""

    url: str
    database: str
    username: str | None = None
    password: str | None = None
    precision: str = DEFAULT_PRECISION
    verify_ssl: bool = True
    timeout: float = 10.0


class ArcadeDBClient:
    """Minimal async client for ArcadeDB time-series ingestion."""

    def __init__(self, session: ClientSession, config: ArcadeDBClientConfig) -> None:
        self._session = session
        self._config = config
        self._base_url = URL(config.url.rstrip("/"))

    async def async_ping(self) -> None:
        """Validate that the ArcadeDB server is reachable and credentials work."""
        url = self._url("/api/v1/server").with_query({"mode": "basic"})
        async with self._session.get(
            url,
            auth=self._auth,
            ssl=self._ssl,
            timeout=self._config.timeout,
        ) as response:
            await self._raise_for_status(response.status, await response.text())

    async def async_database_exists(self) -> bool:
        """Return whether the configured database exists."""
        url = self._url(f"/api/v1/exists/{quote(self._config.database, safe='')}")
        async with self._session.get(
            url,
            auth=self._auth,
            ssl=self._ssl,
            timeout=self._config.timeout,
        ) as response:
            body = await response.text()
            await self._raise_for_status(response.status, body)
            return '"result":true' in body.replace(" ", "").lower()

    async def async_write_lines(self, lines: Sequence[str]) -> None:
        """Write line protocol records to ArcadeDB."""
        if not lines:
            return

        url = self._url(
            f"/api/v1/ts/{quote(self._config.database, safe='')}/write"
        ).with_query({"precision": self._config.precision})
        async with self._session.post(
            url,
            auth=self._auth,
            data="\n".join(lines),
            headers={"Content-Type": "text/plain"},
            ssl=self._ssl,
            timeout=self._config.timeout,
        ) as response:
            await self._raise_for_status(response.status, await response.text())

    @property
    def _auth(self) -> BasicAuth | None:
        if self._config.username is None:
            return None
        return BasicAuth(self._config.username, self._config.password or "")

    @property
    def _ssl(self) -> bool | None:
        return None if self._config.verify_ssl else False

    def _url(self, path: str) -> URL:
        base_path = self._base_url.path.rstrip("/")
        full_path = f"{base_path}{path}" if base_path else path
        return self._base_url.with_path(full_path)

    @staticmethod
    async def _raise_for_status(status: int, body: str) -> None:
        if 200 <= status < 300:
            return
        message = body.strip() or f"HTTP {status}"
        if status in (401, 403):
            raise ArcadeDBAuthError("ArcadeDB authentication failed")
        if status in (408, 425, 429) or status >= 500:
            raise ArcadeDBTransientError(f"Temporary ArcadeDB failure: {message}")
        raise ArcadeDBPermanentError(f"ArcadeDB rejected the request: {message}")
