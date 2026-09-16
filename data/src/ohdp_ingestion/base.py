"""Shared ingestion primitives: a thin HTTP client wrapper and the source contract."""

from __future__ import annotations

import abc
from collections.abc import Iterator
from typing import Any

import httpx

from ohdp_shared import get_logger

log = get_logger(__name__)

_DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
_DEFAULT_LIMITS = httpx.Limits(max_connections=8, max_keepalive_connections=4)


def http_client(base_url: str, headers: dict[str, str] | None = None) -> httpx.Client:
    return httpx.Client(
        base_url=base_url,
        headers=headers or {},
        timeout=_DEFAULT_TIMEOUT,
        limits=_DEFAULT_LIMITS,
        follow_redirects=True,
    )


class Source(abc.ABC):
    """A public data source. Implementations must be pure readers — no writes,
    no mutation of upstream state, deterministic given a time window."""

    name: str
    raw_table: str

    @abc.abstractmethod
    def fetch(self, *, since: str | None = None) -> Iterator[dict[str, Any]]:
        """Yield raw records. Pagination and retry live here; validation does not."""
        raise NotImplementedError
