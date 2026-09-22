"""Shared ingestion primitive: a thin HTTP client wrapper used by the catalog
scrapers and hand-written literature clients."""

from __future__ import annotations

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
