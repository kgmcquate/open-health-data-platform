"""The `/api/topics` routes (hub_api.content) — read OpenMetadata's
Consumer-aligned domains and their assets, not a table, and must degrade to
`[]` (or, for a section of the detail bundle, an empty list within it) rather
than 503 when the catalog is unreachable or unconfigured."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from hub_api.main import app
from ohdp_agent.domains import CatalogAsset, Domain, DomainsError
from ohdp_shared import settings


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


TOPIC = Domain(
    id="1",
    name="Infectious Disease",
    description="Case counts and outbreak surveillance.",
    catalog_url="http://om/domain/Infectious%20Disease",
)


def test_no_jwt_configured_degrades_to_empty_list(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "openmetadata_jwt", "")

    response = client.get("/api/topics")

    assert response.status_code == 200
    assert response.json() == []


def test_consumer_aligned_domains_are_returned_as_topics(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "openmetadata_jwt", "s3cret")

    async def fake_list(self: object, domain_type: str) -> list[Domain]:
        assert domain_type == "Consumer-aligned"
        return [TOPIC]

    monkeypatch.setattr("ohdp_agent.domains.DomainsClient.list_by_type", fake_list)

    response = client.get("/api/topics")

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": "1",
            "name": "Infectious Disease",
            "description": "Case counts and outbreak surveillance.",
            "catalog_url": "http://om/domain/Infectious%20Disease",
        }
    ]


def test_a_catalog_error_degrades_to_empty_list_not_a_503(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "openmetadata_jwt", "s3cret")

    async def fail(self: object, domain_type: str) -> list[Domain]:
        raise DomainsError("OpenMetadata returned 503")

    monkeypatch.setattr("ohdp_agent.domains.DomainsClient.list_by_type", fail)

    response = client.get("/api/topics")

    assert response.status_code == 200
    assert response.json() == []


# --------------------------------------------------------------- /api/topics/{name}


def _patch_topic_bundle(
    monkeypatch: pytest.MonkeyPatch,
    *,
    topics: list[Domain],
    tables: list[CatalogAsset] | None = None,
    metrics: list[CatalogAsset] | None = None,
    sources: list[Domain] | None = None,
) -> None:
    async def fake_list_by_type(self: object, domain_type: str) -> list[Domain]:
        return topics

    async def fake_assets(
        self: object, name: str, entity_type: str, **kwargs: object
    ) -> list[CatalogAsset]:
        return (metrics or []) if entity_type == "metric" else (tables or [])

    async def fake_sources(self: object, table_fqns: list[str]) -> list[Domain]:
        return sources or []

    monkeypatch.setattr(settings, "openmetadata_jwt", "s3cret")
    monkeypatch.setattr("ohdp_agent.domains.DomainsClient.list_by_type", fake_list_by_type)
    monkeypatch.setattr("ohdp_agent.domains.DomainsClient.assets_in_domain", fake_assets)
    monkeypatch.setattr("ohdp_agent.domains.DomainsClient.upstream_sources", fake_sources)


def test_topic_detail_returns_domain_metrics_assets_and_sources(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    table = CatalogAsset(
        id="t1",
        name="NNDSS_WEEKLY",
        description="Weekly case counts.",
        entity_type="table",
        fqn="snowflake.CURATED.INFECTIOUS_DISEASE.NNDSS_WEEKLY",
        catalog_url="http://om/table/snowflake.CURATED.INFECTIOUS_DISEASE.NNDSS_WEEKLY",
    )
    metric = CatalogAsset(
        id="m1",
        name="total_cases",
        description="Reported cases.",
        entity_type="metric",
        fqn="infectious_disease__nndss_weekly__total_cases",
        catalog_url="http://om/metric/infectious_disease__nndss_weekly__total_cases",
    )
    cdc = Domain(id="2", name="CDC", description="", catalog_url="http://om/domain/CDC")
    _patch_topic_bundle(
        monkeypatch, topics=[TOPIC], tables=[table], metrics=[metric], sources=[cdc]
    )

    response = client.get("/api/topics/Infectious Disease")

    assert response.status_code == 200
    body = response.json()
    assert body["topic"]["name"] == "Infectious Disease"
    assert [a["name"] for a in body["assets"]] == ["NNDSS_WEEKLY"]
    assert [m["name"] for m in body["metrics"]] == ["total_cases"]
    assert [s["name"] for s in body["sources"]] == ["CDC"]


def test_topic_detail_404s_for_an_unknown_name(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_topic_bundle(monkeypatch, topics=[TOPIC])

    response = client.get("/api/topics/Not A Real Topic")

    assert response.status_code == 404


def test_topic_detail_503s_when_catalog_not_configured(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "openmetadata_jwt", "")

    response = client.get("/api/topics/Infectious Disease")

    assert response.status_code == 503


def test_topic_detail_degrades_assets_section_independently(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broken assets/metrics search must not blank the whole page — the
    domain and its other sections still come back."""
    monkeypatch.setattr(settings, "openmetadata_jwt", "s3cret")

    async def fake_list_by_type(self: object, domain_type: str) -> list[Domain]:
        return [TOPIC]

    async def failing_assets(
        self: object, name: str, entity_type: str, **kwargs: object
    ) -> list[CatalogAsset]:
        raise DomainsError("OpenMetadata returned 503")

    async def fake_sources(self: object, table_fqns: list[str]) -> list[Domain]:
        return []

    monkeypatch.setattr("ohdp_agent.domains.DomainsClient.list_by_type", fake_list_by_type)
    monkeypatch.setattr("ohdp_agent.domains.DomainsClient.assets_in_domain", failing_assets)
    monkeypatch.setattr("ohdp_agent.domains.DomainsClient.upstream_sources", fake_sources)

    response = client.get("/api/topics/Infectious Disease")

    assert response.status_code == 200
    body = response.json()
    assert body["topic"]["name"] == "Infectious Disease"
    assert body["metrics"] == []
    assert body["assets"] == []
