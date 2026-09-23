"""Read-only REST client for the OpenMetadata reads behind the Topics pages.

A *topic* is a Consumer-aligned OpenMetadata Domain — the subject areas that
mirror dbt's `curated/` marts (Infectious Disease, Respiratory, ...), as
opposed to the Source-aligned domains that name an upstream data provider
(CDC, CMS, WHO). Both are seeded by
`data/src/ohdp_orchestration/assets/openmetadata_seed_sync.py` from
`data/src/ohdp_orchestration/seed/domains.yml`, so editing a domain in the
catalog is what updates the site — no redeploy, no second copy to keep in
sync.

Three reads, all feeding `hub_api.content`'s `/api/topics` routes:

    list_by_type      the topics themselves (and, internally, the
                      Source-aligned domains `upstream_sources` matches against)
    assets_in_domain  a topic's tables and metrics, via the search index
    upstream_sources  which providers a topic's tables came from, via lineage

This hits OM's plain REST API, not the MCP endpoint `ohdp_agent.catalog`
talks to: these are flat reads with no need for JSON-RPC or an LLM tool-call
allowlist, and the REST response also gives back `description` as the plain
Markdown OM stores rather than the HTML-escaped copy the MCP search index
serves.

**On the search filter.** `assets_in_domain` matches `domains.displayName.keyword`,
not `domains.fullyQualifiedName`. The FQN field is indexed with a lowercase
normalizer, so a `term` clause carrying a domain's real casing silently
matches nothing — a clean, wrong zero rather than an error. Matching on
displayName is also what picks up *inherited* domains: `openmetadata_seed_sync`
attaches a domain to the `CURATED.<AREA>` DatabaseSchema, and OM propagates
it down to every table in that schema, which is the whole join this module
relies on.

**On the cache.** These pages are public and unauthenticated, and one topic
page is otherwise five-plus OM round trips. Every read here is cached in
process for `_CACHE_TTL_SECONDS`; the catalog changes on a daily sync at
most, so serving a ten-minute-old topic list is free accuracy-wise and keeps
a hot page off OM's back.
"""

from __future__ import annotations

import html
import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from ohdp_shared import get_logger

log = get_logger(__name__)

# How long a read stays fresh. The catalog is rebuilt by a nightly Dagster
# sync, so this only has to be short enough that a hand edit in OM's UI shows
# up while someone is still looking for it.
_CACHE_TTL_SECONDS = 600

# Upstream hops to walk from a curated mart before giving up on finding the
# provider it came from. 3 is also OM's own server-side ceiling for this
# endpoint; the longest real chain today is exactly that
# (CURATED.<area> <- CURATED.CORE <- CLEAN.STG_<source> <- RAW.<source>).
_UPSTREAM_DEPTH = 3


class DomainsError(RuntimeError):
    """OpenMetadata's REST API was unreachable or refused the request."""


@dataclass(frozen=True)
class Domain:
    id: str
    name: str
    description: str
    catalog_url: str


@dataclass(frozen=True)
class CatalogAsset:
    """One table or metric inside a domain, as a topic page lists it."""

    id: str
    name: str
    description: str
    entity_type: str
    fqn: str
    catalog_url: str


class DomainsClient:
    """Reads domains, their assets and their provenance from OpenMetadata."""

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
        # REST calls always use _base_url, which is the internal address and
        # isn't reachable from a user's browser.
        self._link_base_url = (link_base_url or base_url).rstrip("/")
        self._jwt = jwt
        self._timeout = timeout
        self._client = client

    # ------------------------------------------------------------ transport

    async def _get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        """One GET against OM's REST API, cached by (path, params).

        Raises `DomainsError` on any non-2xx so every caller in `hub_api.content`
        can use the one degrade-to-empty `except` it already has.
        """
        cache_key = (self._base_url, path, json.dumps(params, sort_keys=True))
        cached = _cache_get(cache_key)
        if cached is not None:
            payload: dict[str, Any] = cached
            return payload

        headers = {"Authorization": f"Bearer {self._jwt}"}
        url = f"{self._base_url}{path}"
        if self._client is not None:
            response = await self._client.get(url, params=params, headers=headers)
        else:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(url, params=params, headers=headers)

        if response.status_code >= 400:
            log.warning("om_read_failed", path=path, status=response.status_code)
            raise DomainsError(f"OpenMetadata returned {response.status_code}")

        body: dict[str, Any] = response.json()
        _cache_put(cache_key, body)
        return body

    # ---------------------------------------------------------------- reads

    async def list_by_type(self, domain_type: str) -> list[Domain]:
        """Every domain of one `domainType`, sorted by name.

        `"Consumer-aligned"` is the topic list; `"Source-aligned"` is the set
        of upstream providers `upstream_sources` resolves against.
        """
        body = await self._get(
            "/api/v1/domains",
            {"limit": "200", "fields": "domainType,description"},
        )
        domains = [
            _to_domain(self._link_base_url, raw)
            for raw in body.get("data", [])
            if raw.get("domainType") == domain_type
        ]
        domains.sort(key=lambda d: d.name)
        return domains

    async def assets_in_domain(
        self,
        domain_name: str,
        entity_type: str,
        *,
        limit: int = 100,
    ) -> list[CatalogAsset]:
        """Every asset of one type carrying `domain_name`, directly or
        inherited from its schema — see the module docstring on why this
        matches `displayName.keyword`."""
        query_filter = {
            "query": {
                "bool": {
                    "must": [
                        {"term": {"entityType": entity_type}},
                        {"term": {"domains.displayName.keyword": domain_name}},
                    ]
                }
            }
        }
        body = await self._get(
            "/api/v1/search/query",
            {
                "q": "*",
                "index": "dataAsset",
                "from": "0",
                "size": str(limit),
                "query_filter": json.dumps(query_filter),
            },
        )
        assets = [
            _to_asset(self._link_base_url, hit.get("_source", {}), entity_type)
            for hit in body.get("hits", {}).get("hits", [])
        ]
        assets.sort(key=lambda a: a.name)
        return assets

    async def topics_for_tables(self, table_names: list[str]) -> list[str]:
        """Which topics own these curated tables, by bare table name.

        This is the join behind a saved dashboard's topics: a Cube cube is
        named for the dbt model it wraps, that model is a table in
        `CURATED.<AREA>`, and the schema's Consumer-aligned domain is
        inherited by every table under it (see the module docstring on
        `displayName.keyword`). So a dashboard lands on the right topic page
        because of what it queries — nobody has to label it, and a model is
        never asked to pick a topic it could get wrong.

        Matching is on the bare name, lowercased, against both an asset's
        display name and the last segment of its FQN: Snowflake upper-cases
        identifiers, and OM may carry a friendlier `displayName` than the
        table's own. A name matching nothing contributes nothing — an
        unmatched cube means no topic, never a guessed one.
        """
        wanted = {name.strip().lower() for name in table_names if name.strip()}
        if not wanted:
            return []
        matched: list[str] = []
        for topic in await self.list_by_type("Consumer-aligned"):
            assets = await self.assets_in_domain(topic.name, "table")
            if any(wanted & _table_keys(asset) for asset in assets):
                matched.append(topic.name)
        return matched

    async def upstream_sources(self, table_fqns: list[str]) -> list[Domain]:
        """The Source-aligned domains that `table_fqns` ultimately read from.

        Walks each curated table's lineage back to the raw and staging tables
        it was built from, then asks which domain owns those tables' schemas.
        `openmetadata_seed_sync` attaches CDC to `snowflake.RAW.CDC` and
        `snowflake.CLEAN.STG_CDC`, so a mart's provenance falls out of the
        graph rather than needing a second hand-maintained mapping.

        A table with no ingested lineage contributes nothing; the result is
        deduped and sorted, and is empty rather than an error when no upstream
        schema belongs to a provider.
        """
        upstream_fqns: set[str] = set()
        for table_fqn in table_fqns:
            body = await self._get(
                f"/api/v1/lineage/table/name/{quote(table_fqn, safe='')}",
                {"upstreamDepth": str(_UPSTREAM_DEPTH), "downstreamDepth": "0"},
            )
            # EntityLineage's `nodes` (type/entityLineage.py) is a flat list
            # of every node in the graph *except* the root, which comes back
            # separately under `entity` — downstreamDepth=0 means every node
            # here is upstream of table_fqn.
            for node in body.get("nodes", []):
                fqn = str(node.get("fullyQualifiedName", ""))
                if fqn and fqn != table_fqn:
                    upstream_fqns.add(fqn)

        # A table FQN is service.database.schema.table, so dropping the last
        # part is the schema — quote-safe, since a quoted segment containing a
        # dot is always the *name* part of an earlier component.
        schema_fqns = {fqn.rsplit(".", 1)[0] for fqn in upstream_fqns}

        domain_names: set[str] = set()
        for schema_fqn in sorted(schema_fqns):
            body = await self._get(
                f"/api/v1/databaseSchemas/name/{quote(schema_fqn, safe='')}",
                {"fields": "domains"},
            )
            for raw in body.get("domains", []):
                domain_names.add(str(raw.get("displayName") or raw.get("name", "")))

        # Only providers, never the topic's own Consumer-aligned domain: the
        # schema entity's `domains` carries no `domainType`, so the filter is
        # an intersection with the Source-aligned list rather than a field test.
        sources = await self.list_by_type("Source-aligned")
        return [source for source in sources if source.name in domain_names]


def _table_keys(asset: CatalogAsset) -> set[str]:
    """The names a table might be recognised by, lowercased."""
    return {asset.name.strip().lower(), asset.fqn.rsplit(".", 1)[-1].strip().lower()}


def _to_domain(base_url: str, raw: dict[str, Any]) -> Domain:
    fqn = str(raw.get("fullyQualifiedName") or raw.get("name", ""))
    return Domain(
        id=str(raw.get("id", fqn)),
        name=str(raw.get("displayName") or raw.get("name", "")),
        description=html.unescape(str(raw.get("description", ""))),
        catalog_url=f"{base_url}/domain/{quote(fqn, safe='')}",
    )


def _to_asset(base_url: str, raw: dict[str, Any], entity_type: str) -> CatalogAsset:
    fqn = str(raw.get("fullyQualifiedName") or raw.get("name", ""))
    return CatalogAsset(
        id=str(raw.get("id", fqn)),
        name=str(raw.get("displayName") or raw.get("name", "")),
        description=html.unescape(str(raw.get("description") or "")),
        entity_type=str(raw.get("entityType") or entity_type),
        fqn=fqn,
        # OM's UI routes an asset at /<entityType>/<fqn>, the same shape
        # _to_domain uses for /domain/<fqn>.
        catalog_url=f"{base_url}/{entity_type}/{quote(fqn, safe='')}",
    )


# ------------------------------------------------------------------- cache
# Deliberately a module-level dict rather than anything with eviction: the
# key space is bounded by the number of domains and their assets (tens of
# entries), and every value is a decoded OM response measured in kilobytes.

_cache: dict[tuple[str, str, str], tuple[float, dict[str, Any]]] = {}


def _cache_get(key: tuple[str, str, str]) -> dict[str, Any] | None:
    entry = _cache.get(key)
    if entry is None:
        return None
    expires_at, value = entry
    if expires_at < time.monotonic():
        del _cache[key]
        return None
    return value


def _cache_put(key: tuple[str, str, str], value: dict[str, Any]) -> None:
    _cache[key] = (time.monotonic() + _CACHE_TTL_SECONDS, value)


def clear_cache() -> None:
    """Drop every cached OM response. For tests, which need each case's
    `MockTransport` to actually be hit."""
    _cache.clear()
