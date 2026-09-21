"""Read-only REST client for OpenMetadata's Domain entities.

The hub's "Data Sources" page (`hub_api.content`) used to be backed by a
hand-curated table; it now reads Source-aligned domains straight from
OpenMetadata (seeded by `data/src/ohdp_orchestration/assets/openmetadata_seed_sync.py`
from `data/src/ohdp_orchestration/seed/domains.yml`), so editing a domain in
the catalog is what updates the page — no redeploy, no second copy to keep in
sync.

This hits OM's plain REST API (`/api/v1/domains`), not the MCP endpoint
`ohdp_agent.catalog.CatalogClient` talks to: a flat list read has no need for
JSON-RPC or an LLM tool-call allowlist, and the REST response also gives back
`description` as the plain Markdown OM stores rather than the HTML-escaped
copy the MCP search index serves.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from ohdp_shared import get_logger

log = get_logger(__name__)


class DomainsError(RuntimeError):
    """OpenMetadata's REST API was unreachable or refused the request."""


@dataclass(frozen=True)
class Domain:
    id: str
    name: str
    description: str
    catalog_url: str


class DomainsClient:
    """Reads Domain entities from OpenMetadata's REST API."""

    def __init__(
        self,
        base_url: str,
        jwt: str,
        *,
        link_base_url: str | None = None,
        timeout: float = 15.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        # Public hostname for the catalog_url links returned to callers; the
        # REST calls above always use _base_url, which is the internal
        # address and isn't reachable from a user's browser.
        self._link_base_url = (link_base_url or base_url).rstrip("/")
        self._jwt = jwt
        self._timeout = timeout
        self._client = client

    async def list_source_aligned(self) -> list[Domain]:
        """Every Source-aligned domain — the ones that map to an actual
        upstream data provider, as opposed to the Consumer-aligned domains
        that mirror dbt's curated/ subject areas and have no place on a
        "where does the data come from" page."""
        headers = {"Authorization": f"Bearer {self._jwt}"}
        params = {"limit": "200", "fields": "domainType,description"}
        url = f"{self._base_url}/api/v1/domains"

        if self._client is not None:
            response = await self._client.get(url, params=params, headers=headers)
        else:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(url, params=params, headers=headers)

        if response.status_code >= 400:
            log.warning("om_domains_failed", status=response.status_code)
            raise DomainsError(f"OpenMetadata returned {response.status_code}")

        domains = [
            _to_domain(self._link_base_url, raw)
            for raw in response.json().get("data", [])
            if raw.get("domainType") == "Source-aligned"
        ]
        domains.sort(key=lambda d: d.name)
        return domains


def _to_domain(base_url: str, raw: dict[str, Any]) -> Domain:
    fqn = str(raw.get("fullyQualifiedName") or raw.get("name", ""))
    return Domain(
        id=str(raw.get("id", fqn)),
        name=str(raw.get("displayName") or raw.get("name", "")),
        description=html.unescape(str(raw.get("description", ""))),
        catalog_url=f"{base_url}/domain/{quote(fqn, safe='')}",
    )
