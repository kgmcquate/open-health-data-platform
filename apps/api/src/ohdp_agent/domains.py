"""Read-only REST client for the OpenMetadata reads behind the Topics pages.

A *topic* is a Consumer-aligned OpenMetadata Domain — the subject areas that
mirror dbt's `curated/` marts (Infectious Disease, Respiratory, ...), as
opposed to the Source-aligned domains that name an upstream data provider
(CDC, CMS, WHO). Both are seeded by
`data/src/ohdp_orchestration/assets/openmetadata_seed_sync.py` from
`data/src/ohdp_orchestration/seed/domains.yml`, so editing a domain in the
catalog is what updates the site — no redeploy, no second copy to keep in
sync.

Four reads, feeding `hub_api.content`'s `/api/topics` and `/api/search`:

    list_by_type      the topics themselves (and, internally, the
                      Source-aligned domains `upstream_sources` matches against)
    assets_in_domain  a topic's tables and metrics, via the search index
    upstream_sources  which providers a topic's tables came from, via lineage
    search            free-text catalog search, via the same search index

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
a hot page off OM's back. `search` shares that cache, which is also why it has
a size cap — see the cache section at the bottom of this module.
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

# The entity types `search` will return. Not a taste call: `_to_asset` builds a
# link as `<om>/<entityType>/<fqn>`, which is a real OM route for exactly these
# two. The `dataAsset` index also carries pipelines, containers, stored
# procedures and glossary terms, whose pages either live at a different path or
# mean nothing to a visitor — dropping them beats linking them wrongly.
SEARCHABLE_ENTITY_TYPES = ("table", "metric")

# Characters Elasticsearch's query_string parser reads as syntax. OM passes `q`
# straight through to it, so a visitor typing `covid (2024` or an unbalanced
# quote would get a 400 back mid-keystroke instead of results. Replaced with
# spaces rather than deleted, so `flu/rsv` searches for two words.
_QUERY_STRING_RESERVED = str.maketrans({ch: " " for ch in '+-=&|!(){}[]^"~*?:\\/<>'})

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

    async def search(self, text: str, *, limit: int = 10) -> list[CatalogAsset]:
        """Free-text search across the catalog's tables and metrics.

        The same `/api/v1/search/query` endpoint `assets_in_domain` uses, with
        the visitor's words in `q` instead of a domain filter. OM's index is
        already built over every asset's name, description and columns and is
        rebuilt by the same sync that seeds the domains, so the hub's search
        bar asks it rather than keeping a second index of its own.

        Restricted to the curated layer: an asset only matches if it carries
        one of the Consumer-aligned (topic) domains, the same join
        `topics_for_tables` relies on to call a mart curated (see the module
        docstring on `displayName.keyword`). Raw and staging tables carry a
        Source-aligned domain instead, never a topic's, so they never show up
        in a visitor's search bar — there's nothing there for them to act on
        yet. A metric's FQN doesn't follow the `service.database.schema.table`
        shape tables do (it's `cube.<domain>.<name>`), which is why this
        filters by domain rather than by parsing the FQN.

        Results come back in OM's relevance order — unlike `assets_in_domain`,
        which lists a whole domain and therefore sorts by name. An empty or
        all-punctuation `text` is not a query and returns `[]` without a round
        trip, and so does a search when there are no topic domains to filter
        against.
        """
        query = search_query(text)
        if not query:
            return []
        topic_names = [topic.name for topic in await self.list_by_type("Consumer-aligned")]
        if not topic_names:
            return []
        query_filter = {
            "query": {
                "bool": {
                    "must": [
                        {"terms": {"entityType": list(SEARCHABLE_ENTITY_TYPES)}},
                        {"terms": {"domains.displayName.keyword": topic_names}},
                    ]
                }
            }
        }
        body = await self._get(
            "/api/v1/search/query",
            {
                "q": query,
                "index": "dataAsset",
                "from": "0",
                "size": str(limit),
                "query_filter": json.dumps(query_filter),
            },
        )
        assets: list[CatalogAsset] = []
        for hit in body.get("hits", {}).get("hits", []):
            raw = hit.get("_source", {})
            entity_type = str(raw.get("entityType", ""))
            # The filter above should have done this; re-checked here because
            # the link built for an unknown type would be a dead one.
            if entity_type in SEARCHABLE_ENTITY_TYPES:
                assets.append(_to_asset(self._link_base_url, raw, entity_type))
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


def search_query(text: str) -> str:
    """`text` as something safe to hand OM's `q` parameter.

    Reserved query_string syntax is stripped (see `_QUERY_STRING_RESERVED`) and
    the final word is left open-ended, so a search bar that fires while someone
    is still typing finds "Diabetes" for "diabet". Earlier words are matched
    whole: they are finished words, and a trailing `*` on each of them only
    widens a query the visitor already narrowed. `""` when nothing survives,
    which callers treat as "no query" rather than as a match-everything `*`.
    """
    words = text.translate(_QUERY_STRING_RESERVED).split()
    if not words:
        return ""
    return " ".join([*words[:-1], f"{words[-1]}*"])


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
# A module-level dict with a TTL and a size cap. The TTL is what this is for
# (see the module docstring); the cap exists because `search` made the key
# space unbounded. The topic reads key on a fixed set of domains and entity
# types — tens of entries, which is why this cache had no eviction at all
# originally — but a search bar keys on whatever anyone types, one entry per
# debounced keystroke, and this process is long-lived.

_CACHE_MAX_ENTRIES = 512


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
    if len(_cache) >= _CACHE_MAX_ENTRIES:
        _evict()
    _cache[key] = (time.monotonic() + _CACHE_TTL_SECONDS, value)


def _evict() -> None:
    """Make room: expired entries first, then the oldest inserted.

    Insertion order is the eviction order (a plain dict keeps it), so this is
    FIFO rather than LRU — a re-read does not refresh an entry's position. That
    is the right way round here: the entries worth keeping are the ones a TTL
    would keep anyway, and the ones filling the dict up are one-off search
    queries nobody types twice.
    """
    now = time.monotonic()
    for key in [key for key, (expires_at, _) in _cache.items() if expires_at < now]:
        del _cache[key]
    while len(_cache) >= _CACHE_MAX_ENTRIES:
        del _cache[next(iter(_cache))]


def clear_cache() -> None:
    """Drop every cached OM response. For tests, which need each case's
    `MockTransport` to actually be hit."""
    _cache.clear()
