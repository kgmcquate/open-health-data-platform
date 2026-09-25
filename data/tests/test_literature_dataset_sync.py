"""Offline checks for `literature_dataset_sync`: Dagster registration (mirrors
`test_cube_metrics_sync.py`'s pattern), the pure Context Center page-request
shape, and the node-reading helpers (`_tag`, `_relevance_query`). Nothing here
hits the network or OpenMetadata."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from metadata.generated.schema.entity.data.article import Article

from ohdp_ingestion.literature.openalex import OpenAlexWork
from ohdp_orchestration.assets.literature_dataset_sync import (
    _create_page_request,
    _page_name,
    _relevance_query,
    _tag,
)


def _catalog_node(
    *, enabled: bool = True, description: str = "Child fatalities by state."
) -> dict[str, Any]:
    return {
        "assetKey": {"path": ["sources", "healthdata_gov", "perpetrators_trend"]},
        "description": description,
        "tags": [
            {"key": "domain", "value": "healthdata_gov"},
            {"key": "enabled", "value": "true" if enabled else "false"},
        ],
        "dependedByKeys": [{"path": ["ingestion", "healthdata_gov", "perpetrators_trend"]}],
    }


def _openalex_work(
    *,
    openalex_id: str = "W123",
    doi: str = "10.1000/xyz",
    publication_date: date | None = date(2023, 5, 1),
) -> OpenAlexWork:
    return OpenAlexWork(
        openalex_id=openalex_id,
        title="A Paper About Something",
        doi=doi,
        pmid="12345678",
        pmcid="PMC123",
        cited_by_count=500,
        publication_year=2023,
        publication_date=publication_date,
        venue="Journal of Testing",
        authors="A. One, B. Two",
        abstract="This is an abstract.",
    )


def test_dagster_job_and_schedule_are_registered() -> None:
    from ohdp_orchestration.definitions import defs

    rd = defs.get_repository_def()
    assert "literature_dataset_sync_job" in {j.name for j in rd.get_all_jobs()}
    schedule = next(s for s in rd.schedule_defs if s.name == "literature_dataset_sync_schedule")
    assert schedule.job_name == "literature_dataset_sync_job"
    assert schedule.default_status.value == "STOPPED"


def test_literature_dataset_sync_asset_is_registered() -> None:
    from ohdp_orchestration.definitions import defs

    graph = defs.resolve_asset_graph()
    keys = {k.to_user_string() for k in graph.get_all_asset_keys()}
    assert "openmetadata/literature_dataset_sync" in keys


def test_tag_reads_matching_key() -> None:
    node = _catalog_node(enabled=True)
    assert _tag(node, "enabled") == "true"
    assert _tag(node, "domain") == "healthdata_gov"
    assert _tag(node, "missing") is None


def test_relevance_query_uses_description() -> None:
    node = _catalog_node(description="Child fatalities by state.")
    assert _relevance_query(node) == "Child fatalities by state."


def test_relevance_query_falls_back_to_asset_key_when_no_description() -> None:
    node = _catalog_node(description="")
    assert _relevance_query(node) == "perpetrators_trend"


def test_page_name_is_stable_and_slug_safe() -> None:
    assert _page_name("W123") == "openalex-w123"
    assert _page_name("W3128646645") == "openalex-w3128646645"


def test_create_page_request_shape() -> None:
    request = _create_page_request(_openalex_work())

    assert str(request.name.root) == "openalex-w123"
    assert request.displayName == "A Paper About Something"
    assert request.pageType.value == "Article"

    assert request.description is not None
    body = str(request.description.root)
    assert "A Paper About Something" in body
    assert "**Citations:** 500" in body
    assert "https://doi.org/10.1000/xyz" in body
    assert "pubmed.ncbi.nlm.nih.gov/12345678" in body
    assert "This is an abstract." in body


def test_create_page_request_serializes_publication_date_as_utc_iso8601() -> None:
    request = _create_page_request(_openalex_work())
    payload = json.loads(request.model_dump_json(exclude_none=True))

    assert payload["page"]["publicationDate"] == "2023-05-01T00:00:00Z"


def test_create_page_request_handles_missing_publication_date() -> None:
    request = _create_page_request(_openalex_work(publication_date=None))
    assert isinstance(request.page, Article)
    assert request.page.publicationDate is None


def test_create_page_request_uses_openalex_landing_page_without_doi() -> None:
    request = _create_page_request(_openalex_work(doi=""))
    assert request.description is not None
    body = str(request.description.root)
    assert "https://openalex.org/W123" in body
