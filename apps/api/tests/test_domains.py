"""OpenMetadata REST client for Topics and their catalog assets (ohdp_agent.domains)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from ohdp_agent import domains as domains_module
from ohdp_agent.domains import (
    CatalogAsset,
    Domain,
    DomainsClient,
    DomainsError,
    search_query,
)


@pytest.fixture(autouse=True)
def _clear_om_cache() -> None:
    """Every test builds its own `MockTransport`; without this, a later test
    reusing the same (base_url, path, params) as an earlier one would get
    that earlier test's cached response instead of hitting its own mock."""
    domains_module.clear_cache()


def _mock(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


DOMAINS_PAYLOAD = {
    "data": [
        {
            "id": "ab1f1555-c376-41aa-bf21-665d3ee4af53",
            "name": "HealthData.gov",
            "fullyQualifiedName": '"HealthData.gov"',
            "domainType": "Source-aligned",
            "description": "Datasets from HealthData.gov&#39;s Socrata catalog.",
        },
        {
            "id": "b2c2c2c2-c376-41aa-bf21-665d3ee4af53",
            "name": "NCHS",
            "fullyQualifiedName": "CDC.NCHS",
            "domainType": "Source-aligned",
            "description": "Vital statistics and health-survey data.",
        },
        {
            "id": "c3c3c3c3-c376-41aa-bf21-665d3ee4af53",
            "name": "Respiratory",
            "fullyQualifiedName": "Respiratory",
            "domainType": "Consumer-aligned",
            "description": "RSV, influenza, and COVID-19 burden.",
        },
        {
            "id": "d4d4d4d4-c376-41aa-bf21-665d3ee4af53",
            "name": "Infectious Disease",
            "fullyQualifiedName": "Infectious Disease",
            "domainType": "Consumer-aligned",
            "description": "Case counts and outbreak surveillance.",
        },
    ]
}


async def test_only_source_aligned_domains_are_returned() -> None:
    client = DomainsClient(
        "http://om", "jwt", client=_mock(lambda r: httpx.Response(200, json=DOMAINS_PAYLOAD))
    )

    domains = await client.list_by_type("Source-aligned")

    assert [d.name for d in domains] == ["HealthData.gov", "NCHS"]


async def test_only_consumer_aligned_domains_are_returned() -> None:
    """The topic list — the mirror of the Source-aligned filter above."""
    client = DomainsClient(
        "http://om", "jwt", client=_mock(lambda r: httpx.Response(200, json=DOMAINS_PAYLOAD))
    )

    topics = await client.list_by_type("Consumer-aligned")

    assert [t.name for t in topics] == ["Infectious Disease", "Respiratory"]


async def test_results_are_sorted_by_name() -> None:
    client = DomainsClient(
        "http://om", "jwt", client=_mock(lambda r: httpx.Response(200, json=DOMAINS_PAYLOAD))
    )

    domains = await client.list_by_type("Source-aligned")

    assert domains[0].name < domains[1].name


async def test_description_html_entities_are_decoded() -> None:
    client = DomainsClient(
        "http://om", "jwt", client=_mock(lambda r: httpx.Response(200, json=DOMAINS_PAYLOAD))
    )

    domains = await client.list_by_type("Source-aligned")

    assert "&#39;" not in domains[0].description
    assert "HealthData.gov's Socrata catalog" in domains[0].description


async def test_catalog_url_is_built_from_the_fqn_and_encoded() -> None:
    client = DomainsClient(
        "http://om", "jwt", client=_mock(lambda r: httpx.Response(200, json=DOMAINS_PAYLOAD))
    )

    domains = await client.list_by_type("Source-aligned")
    nested = next(d for d in domains if d.name == "NCHS")

    assert nested.catalog_url == "http://om/domain/CDC.NCHS"


async def test_catalog_url_uses_link_base_url_when_given() -> None:
    """The REST call still targets the internal base_url; only the returned
    catalog_url should point at a separate public hostname."""
    seen_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        return httpx.Response(200, json=DOMAINS_PAYLOAD)

    client = DomainsClient(
        "http://internal-om:8585",
        "jwt",
        link_base_url="https://catalog.example.org",
        client=_mock(handler),
    )

    domains = await client.list_by_type("Source-aligned")
    nested = next(d for d in domains if d.name == "NCHS")

    assert nested.catalog_url == "https://catalog.example.org/domain/CDC.NCHS"
    assert seen_urls[0].startswith("http://internal-om:8585/")


async def test_bearer_token_is_sent() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["authorization"]
        return httpx.Response(200, json={"data": []})

    await DomainsClient("http://om", "s3cret", client=_mock(handler)).list_by_type("Source-aligned")

    assert seen["auth"] == "Bearer s3cret"


async def test_an_error_status_raises() -> None:
    client = DomainsClient(
        "http://om", "jwt", client=_mock(lambda r: httpx.Response(503, text="unavailable"))
    )

    with pytest.raises(DomainsError, match="503"):
        await client.list_by_type("Source-aligned")


def test_domain_is_frozen() -> None:
    domain = Domain(id="1", name="OpenAQ", description="", catalog_url="")
    with pytest.raises(AttributeError):
        domain.name = "changed"  # type: ignore[misc]


# ---------------------------------------------------------------- assets_in_domain

ASSETS_SEARCH_PAYLOAD = {
    "hits": {
        "hits": [
            {
                "_source": {
                    "id": "e1e1e1e1-0000-0000-0000-000000000001",
                    "name": "NNDSS_WEEKLY",
                    "fullyQualifiedName": "snowflake.CURATED.INFECTIOUS_DISEASE.NNDSS_WEEKLY",
                    "entityType": "table",
                    "description": "NNDSS weekly case counts.",
                },
            },
            {
                "_source": {
                    "id": "e2e2e2e2-0000-0000-0000-000000000002",
                    "name": "WASTEWATER_ACTIVITY_WEEKLY",
                    "fullyQualifiedName": (
                        "snowflake.CURATED.INFECTIOUS_DISEASE.WASTEWATER_ACTIVITY_WEEKLY"
                    ),
                    "entityType": "table",
                    "description": "Wastewater viral activity.",
                },
            },
        ]
    }
}


async def test_assets_in_domain_filters_by_entity_type_and_domain() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["query_filter"] = request.url.params["query_filter"]
        return httpx.Response(200, json=ASSETS_SEARCH_PAYLOAD)

    client = DomainsClient("http://om", "jwt", client=_mock(handler))

    assets = await client.assets_in_domain("Infectious Disease", "table")

    assert seen["path"] == "/api/v1/search/query"
    assert '"entityType": "table"' in seen["query_filter"]
    assert '"domains.displayName.keyword": "Infectious Disease"' in seen["query_filter"]
    assert [a.name for a in assets] == ["NNDSS_WEEKLY", "WASTEWATER_ACTIVITY_WEEKLY"]


async def test_assets_in_domain_builds_catalog_url_from_entity_type_and_fqn() -> None:
    client = DomainsClient(
        "http://om", "jwt", client=_mock(lambda r: httpx.Response(200, json=ASSETS_SEARCH_PAYLOAD))
    )

    assets = await client.assets_in_domain("Infectious Disease", "table")
    nndss = next(a for a in assets if a.name == "NNDSS_WEEKLY")

    assert nndss.catalog_url == "http://om/table/snowflake.CURATED.INFECTIOUS_DISEASE.NNDSS_WEEKLY"


def test_catalog_asset_is_frozen() -> None:
    asset = CatalogAsset(
        id="1", name="T", description="", entity_type="table", fqn="", catalog_url=""
    )
    with pytest.raises(AttributeError):
        asset.name = "changed"  # type: ignore[misc]


# ---------------------------------------------------------------- upstream_sources

LINEAGE_PAYLOAD = {
    "entity": {"fullyQualifiedName": "snowflake.CURATED.INFECTIOUS_DISEASE.NNDSS_WEEKLY"},
    "nodes": [
        {"fullyQualifiedName": "snowflake.CURATED.CORE.NNDSS_WEEKLY_CASE_COUNTS"},
        {"fullyQualifiedName": "snowflake.CLEAN.STG_CDC.NNDSS_WEEKLY"},
        {"fullyQualifiedName": "snowflake.RAW.CDC.NNDSS_WEEKLY_DATA"},
    ],
}

SCHEMA_DOMAIN_PAYLOADS = {
    "snowflake.CURATED.CORE": {"domains": []},
    "snowflake.CLEAN.STG_CDC": {"domains": [{"displayName": "CDC", "name": "CDC"}]},
    "snowflake.RAW.CDC": {"domains": [{"displayName": "CDC", "name": "CDC"}]},
}


async def test_upstream_sources_ignores_a_resolved_domain_not_in_the_source_aligned_set() -> None:
    """The schemas resolve to a domain named "CDC", but `DOMAINS_PAYLOAD`
    above has no Source-aligned "CDC" entry — the intersection with
    `list_by_type("Source-aligned")` is what keeps a stale or renamed
    provider from showing up as a source."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v1/lineage/table/name/snowflake.CURATED.INFECTIOUS_DISEASE.NNDSS_WEEKLY":
            return httpx.Response(200, json=LINEAGE_PAYLOAD)
        if path.startswith("/api/v1/databaseSchemas/name/"):
            schema_fqn = path.removeprefix("/api/v1/databaseSchemas/name/")
            return httpx.Response(200, json=SCHEMA_DOMAIN_PAYLOADS.get(schema_fqn, {"domains": []}))
        if path == "/api/v1/domains":
            return httpx.Response(200, json=DOMAINS_PAYLOAD)
        raise AssertionError(f"unexpected path: {path}")

    client = DomainsClient("http://om", "jwt", client=_mock(handler))

    sources = await client.upstream_sources(["snowflake.CURATED.INFECTIOUS_DISEASE.NNDSS_WEEKLY"])

    assert sources == []


async def test_upstream_sources_matches_against_source_aligned_domains_by_name() -> None:
    """When the resolved provider domain *is* in the Source-aligned set, it
    comes back — the mirror of the empty-intersection case above."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v1/lineage/table/name/snowflake.CURATED.INFECTIOUS_DISEASE.NNDSS_WEEKLY":
            return httpx.Response(200, json=LINEAGE_PAYLOAD)
        if path.startswith("/api/v1/databaseSchemas/name/"):
            schema_fqn = path.removeprefix("/api/v1/databaseSchemas/name/")
            if schema_fqn == "snowflake.CLEAN.STG_CDC":
                return httpx.Response(
                    200, json={"domains": [{"displayName": "NCHS", "name": "NCHS"}]}
                )
            return httpx.Response(200, json={"domains": []})
        if path == "/api/v1/domains":
            return httpx.Response(200, json=DOMAINS_PAYLOAD)
        raise AssertionError(f"unexpected path: {path}")

    client = DomainsClient("http://om", "jwt", client=_mock(handler))

    sources = await client.upstream_sources(["snowflake.CURATED.INFECTIOUS_DISEASE.NNDSS_WEEKLY"])

    assert [s.name for s in sources] == ["NCHS"]


async def test_upstream_sources_is_empty_for_a_table_with_no_lineage() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/domains":
            return httpx.Response(200, json=DOMAINS_PAYLOAD)
        return httpx.Response(200, json={"entity": {}, "nodes": []})

    client = DomainsClient("http://om", "jwt", client=_mock(handler))

    sources = await client.upstream_sources(["snowflake.CURATED.SOME.TABLE"])

    assert sources == []


# ---------------------------------------------------------------- topics_for_tables


async def test_topics_for_tables_matches_a_cube_name_to_its_domain() -> None:
    """The join behind a saved dashboard's topics: a Cube cube is named for
    the dbt model it wraps, and that model is a table carrying the domain. The
    cube name arrives lowercase, the warehouse table is upper-cased — matching
    has to survive that or every dashboard lands with no topic."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/domains":
            return httpx.Response(200, json=DOMAINS_PAYLOAD)
        query_filter = request.url.params["query_filter"]
        if '"Infectious Disease"' in query_filter:
            return httpx.Response(200, json=ASSETS_SEARCH_PAYLOAD)
        return httpx.Response(200, json={"hits": {"hits": []}})

    client = DomainsClient("http://om", "jwt", client=_mock(handler))

    topics = await client.topics_for_tables(["nndss_weekly"])

    assert topics == ["Infectious Disease"]


async def test_topics_for_tables_matches_on_the_fqns_last_segment_too() -> None:
    """OM may carry a friendlier `displayName` than the table's own name; the
    FQN still ends in the real identifier."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/domains":
            return httpx.Response(200, json=DOMAINS_PAYLOAD)
        renamed = {
            "hits": {
                "hits": [
                    {
                        "_source": {
                            "id": "e1",
                            "name": "Weekly NNDSS cases",
                            "fullyQualifiedName": (
                                "snowflake.CURATED.INFECTIOUS_DISEASE.NNDSS_WEEKLY"
                            ),
                            "entityType": "table",
                        }
                    }
                ]
            }
        }
        return httpx.Response(200, json=renamed)

    client = DomainsClient("http://om", "jwt", client=_mock(handler))

    assert "Infectious Disease" in await client.topics_for_tables(["nndss_weekly"])


async def test_an_uncatalogued_cube_gets_no_topic_rather_than_a_guess() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/domains":
            return httpx.Response(200, json=DOMAINS_PAYLOAD)
        return httpx.Response(200, json=ASSETS_SEARCH_PAYLOAD)

    client = DomainsClient("http://om", "jwt", client=_mock(handler))

    assert await client.topics_for_tables(["not_a_real_cube"]) == []


async def test_topics_for_tables_asks_nothing_when_there_are_no_tables() -> None:
    """No cubes means no OM round trips at all — a save should not pay for a
    catalog read it cannot use."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected call to {request.url}")

    client = DomainsClient("http://om", "jwt", client=_mock(handler))

    assert await client.topics_for_tables([]) == []


# ------------------------------------------------------------------------ search

SEARCH_PAYLOAD = {
    "hits": {
        "hits": [
            {
                "_source": {
                    "id": "f1f1f1f1-0000-0000-0000-000000000001",
                    "name": "NNDSS_WEEKLY",
                    "fullyQualifiedName": "snowflake.CURATED.INFECTIOUS_DISEASE.NNDSS_WEEKLY",
                    "entityType": "table",
                    "description": "NNDSS weekly case counts.",
                },
            },
            {
                "_source": {
                    "id": "f2f2f2f2-0000-0000-0000-000000000002",
                    "name": "weekly_cases",
                    "fullyQualifiedName": "cube.infectious_disease.weekly_cases",
                    "entityType": "metric",
                    "description": "Reported cases per week.",
                },
            },
            {
                "_source": {
                    "id": "f3f3f3f3-0000-0000-0000-000000000003",
                    "name": "nndss_weekly_ingest",
                    "fullyQualifiedName": "dagster.nndss_weekly_ingest",
                    "entityType": "pipeline",
                    "description": "Loads NNDSS.",
                },
            },
        ]
    }
}


def _search_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/api/v1/domains":
        return httpx.Response(200, json=DOMAINS_PAYLOAD)
    return httpx.Response(200, json=SEARCH_PAYLOAD)


async def test_search_keeps_relevance_order_and_both_asset_kinds() -> None:
    """Unlike `assets_in_domain`, which lists a domain and sorts by name, this
    must hand back whatever order OM ranked the hits in."""
    client = DomainsClient("http://om", "jwt", client=_mock(_search_handler))

    hits = await client.search("nndss weekly")

    assert [(h.name, h.entity_type) for h in hits] == [
        ("NNDSS_WEEKLY", "table"),
        ("weekly_cases", "metric"),
    ]


async def test_search_drops_entity_types_it_cannot_link_to() -> None:
    """A pipeline is in the `dataAsset` index but `<om>/pipeline/<fqn>` is not
    the link `_to_asset` would build, so it is filtered rather than offered."""
    client = DomainsClient("http://om", "jwt", client=_mock(_search_handler))

    hits = await client.search("nndss")

    assert "pipeline" not in {h.entity_type for h in hits}


async def test_search_filters_on_the_searchable_entity_types_and_topic_domains() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/domains":
            return httpx.Response(200, json=DOMAINS_PAYLOAD)
        seen["q"] = request.url.params["q"]
        seen["query_filter"] = request.url.params["query_filter"]
        seen["size"] = request.url.params["size"]
        return httpx.Response(200, json=SEARCH_PAYLOAD)

    client = DomainsClient("http://om", "jwt", client=_mock(handler))

    await client.search("respiratory", limit=5)

    assert seen["q"] == "respiratory*"
    assert '"entityType": ["table", "metric"]' in seen["query_filter"]
    assert '"domains.displayName.keyword": ["Infectious Disease", "Respiratory"]' in seen["query_filter"]
    assert seen["size"] == "5"


async def test_search_with_no_topic_domains_makes_no_search_request() -> None:
    """Nothing is curated yet, so there's nothing a free-text search can return."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/domains":
            return httpx.Response(200, json={"data": []})
        raise AssertionError("OM should not have been searched")

    client = DomainsClient("http://om", "jwt", client=_mock(handler))

    assert await client.search("respiratory") == []


async def test_search_with_no_usable_words_makes_no_request() -> None:
    """`"*"` would be a match-everything query — an empty search must be no
    search, not the whole catalog."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("OM should not have been called")

    client = DomainsClient("http://om", "jwt", client=_mock(handler))

    assert await client.search("   ") == []
    assert await client.search("*?!") == []


def test_search_query_leaves_the_last_word_open_ended() -> None:
    assert search_query("diabet") == "diabet*"
    assert search_query("respiratory illness bur") == "respiratory illness bur*"


def test_search_query_strips_elasticsearch_syntax() -> None:
    """A half-typed query is a query, not a 400: OM hands `q` straight to
    Elasticsearch's query_string parser, which would reject these."""
    assert search_query("covid (2024") == "covid 2024*"
    assert search_query('"unbalanced') == "unbalanced*"
    assert search_query("flu/rsv") == "flu rsv*"


# ------------------------------------------------------------------------- cache


async def test_the_cache_is_bounded_once_search_keys_on_free_text() -> None:
    """The cache had no eviction because the topic reads key on a fixed set of
    domains. `search` keys on whatever anyone types — one entry per debounced
    keystroke in a process that runs for weeks — so the cap is what keeps that
    from being a slow leak."""
    client = DomainsClient("http://om", "jwt", client=_mock(_search_handler))

    for index in range(domains_module._CACHE_MAX_ENTRIES + 50):
        await client.search(f"query{index}")

    assert len(domains_module._cache) <= domains_module._CACHE_MAX_ENTRIES


async def test_a_repeated_search_is_served_from_the_cache() -> None:
    """The flip side of the cap: typing the same thing twice makes no new OM
    calls, which is the point of sharing the topic reads' cache. The first
    search costs two calls (the topic-domain lookup, then the search itself);
    the second is served entirely from cache."""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _search_handler(request)

    client = DomainsClient("http://om", "jwt", client=_mock(handler))

    await client.search("respiratory")
    await client.search("respiratory")

    assert calls == 2
