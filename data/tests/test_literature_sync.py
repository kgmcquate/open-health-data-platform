"""Offline checks for `literature_sync`: Dagster registration (mirrors
`test_cube_metrics_sync.py`'s pattern) and the pure Context Center page-request
shape. Nothing here hits the network or OpenMetadata."""

from __future__ import annotations

from datetime import date

from metadata.generated.schema.entity.data.article import Article

from ohdp_ingestion.literature.openalex import OpenAlexWork
from ohdp_ingestion.literature.selection import SelectedWork
from ohdp_orchestration.assets.literature_sync import _create_page_request, _page_name


def test_dagster_job_and_schedule_are_registered() -> None:
    from ohdp_orchestration.definitions import defs

    rd = defs.get_repository_def()
    assert "literature_sync_job" in {j.name for j in rd.get_all_jobs()}
    schedule = next(s for s in rd.schedule_defs if s.name == "literature_sync_schedule")
    assert schedule.job_name == "literature_sync_job"
    # STOPPED by default until the JWT/OpenAlex contact email are confirmed
    # live in the target environment (mirrors every other OM sync schedule).
    assert schedule.default_status.value == "STOPPED"


def test_literature_sync_asset_is_registered() -> None:
    from ohdp_orchestration.definitions import defs

    graph = defs.resolve_asset_graph()
    keys = {k.to_user_string() for k in graph.get_all_asset_keys()}
    assert "openmetadata/literature_sync" in keys


def _selected_work(
    *,
    doi: str = "10.1000/xyz",
    publication_date: date | None = date(2023, 5, 1),
) -> SelectedWork:
    work = OpenAlexWork(
        openalex_id="W123",
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
    return SelectedWork(work=work, domains={"Child Welfare", "Chronic Disease"})


def test_page_name_is_stable_and_slug_safe() -> None:
    assert _page_name("W123") == "openalex-w123"
    assert _page_name("W3128646645") == "openalex-w3128646645"


def test_create_page_request_shape() -> None:
    request = _create_page_request(_selected_work())

    assert str(request.name.root) == "openalex-w123"
    assert request.displayName == "A Paper About Something"
    assert request.pageType.value == "Article"
    assert sorted(str(d.root) for d in request.domains or []) == [
        "Child Welfare",
        "Chronic Disease",
    ]

    assert request.description is not None
    body = str(request.description.root)
    assert "A Paper About Something" in body
    assert "**Citations:** 500" in body
    assert "https://doi.org/10.1000/xyz" in body
    assert "pubmed.ncbi.nlm.nih.gov/12345678" in body
    assert "This is an abstract." in body


def test_create_page_request_handles_missing_publication_date() -> None:
    request = _create_page_request(_selected_work(publication_date=None))
    assert isinstance(request.page, Article)
    assert request.page.publicationDate is None


def test_create_page_request_uses_openalex_landing_page_without_doi() -> None:
    request = _create_page_request(_selected_work(doi=""))
    assert request.description is not None
    body = str(request.description.root)
    assert "https://openalex.org/W123" in body
