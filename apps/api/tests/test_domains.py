"""OpenMetadata REST client for Domains (ohdp_agent.domains)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from ohdp_agent.domains import Domain, DomainsClient, DomainsError


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
    ]
}


async def test_only_source_aligned_domains_are_returned() -> None:
    client = DomainsClient(
        "http://om", "jwt", client=_mock(lambda r: httpx.Response(200, json=DOMAINS_PAYLOAD))
    )

    domains = await client.list_source_aligned()

    assert [d.name for d in domains] == ["HealthData.gov", "NCHS"]


async def test_results_are_sorted_by_name() -> None:
    client = DomainsClient(
        "http://om", "jwt", client=_mock(lambda r: httpx.Response(200, json=DOMAINS_PAYLOAD))
    )

    domains = await client.list_source_aligned()

    assert domains[0].name < domains[1].name


async def test_description_html_entities_are_decoded() -> None:
    client = DomainsClient(
        "http://om", "jwt", client=_mock(lambda r: httpx.Response(200, json=DOMAINS_PAYLOAD))
    )

    domains = await client.list_source_aligned()

    assert "&#39;" not in domains[0].description
    assert "HealthData.gov's Socrata catalog" in domains[0].description


async def test_catalog_url_is_built_from_the_fqn_and_encoded() -> None:
    client = DomainsClient(
        "http://om", "jwt", client=_mock(lambda r: httpx.Response(200, json=DOMAINS_PAYLOAD))
    )

    domains = await client.list_source_aligned()
    nested = next(d for d in domains if d.name == "NCHS")

    assert nested.catalog_url == "http://om/domain/CDC.NCHS"


async def test_bearer_token_is_sent() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["authorization"]
        return httpx.Response(200, json={"data": []})

    await DomainsClient("http://om", "s3cret", client=_mock(handler)).list_source_aligned()

    assert seen["auth"] == "Bearer s3cret"


async def test_an_error_status_raises() -> None:
    client = DomainsClient(
        "http://om", "jwt", client=_mock(lambda r: httpx.Response(503, text="unavailable"))
    )

    with pytest.raises(DomainsError, match="503"):
        await client.list_source_aligned()


def test_domain_is_frozen() -> None:
    domain = Domain(id="1", name="OpenAQ", description="", catalog_url="")
    with pytest.raises(AttributeError):
        domain.name = "changed"  # type: ignore[misc]
