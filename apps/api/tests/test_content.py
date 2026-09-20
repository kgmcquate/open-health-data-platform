"""The `/api/data-sources` route (hub_api.content) — reads OpenMetadata's
Source-aligned domains, not a table, and must degrade to `[]` rather than
503 when the catalog is unreachable or unconfigured."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from hub_api.main import app
from ohdp_agent.domains import Domain, DomainsError
from ohdp_shared import settings


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_no_jwt_configured_degrades_to_empty_list(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "openmetadata_jwt", "")

    response = client.get("/api/data-sources")

    assert response.status_code == 200
    assert response.json() == []


def test_domains_are_returned_as_data_sources(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "openmetadata_jwt", "s3cret")

    async def fake_list(self: object) -> list[Domain]:
        return [
            Domain(id="1", name="OpenAQ", description="Air quality.", catalog_url="http://om/domain/OpenAQ")
        ]

    monkeypatch.setattr("ohdp_agent.domains.DomainsClient.list_source_aligned", fake_list)

    response = client.get("/api/data-sources")

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": "1",
            "name": "OpenAQ",
            "description": "Air quality.",
            "catalog_url": "http://om/domain/OpenAQ",
        }
    ]


def test_a_catalog_error_degrades_to_empty_list_not_a_503(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "openmetadata_jwt", "s3cret")

    async def fail(self: object) -> list[Domain]:
        raise DomainsError("OpenMetadata returned 503")

    monkeypatch.setattr("ohdp_agent.domains.DomainsClient.list_source_aligned", fail)

    response = client.get("/api/data-sources")

    assert response.status_code == 200
    assert response.json() == []
