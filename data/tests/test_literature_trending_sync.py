"""Offline checks for `literature_trending_sync`: Dagster registration and the
pure trending-item shape (`_trending_item`, `_summary`). Nothing here hits the
network or hub-api."""

from __future__ import annotations

from datetime import date

from ohdp_ingestion.literature.openalex import OpenAlexWork
from ohdp_ingestion.literature.selection import SelectedWork
from ohdp_orchestration.assets.literature_trending_sync import (
    _SUMMARY_MAX_CHARS,
    _summary,
    _trending_item,
)


def _selected_work(*, abstract: str = "This is an abstract.") -> SelectedWork:
    work = OpenAlexWork(
        openalex_id="W123",
        title="A Paper About Something",
        doi="10.1000/xyz",
        pmid="12345678",
        pmcid="PMC123",
        cited_by_count=500,
        publication_year=2023,
        publication_date=date(2023, 5, 1),
        venue="Journal of Testing",
        authors="A. One, B. Two",
        abstract=abstract,
    )
    return SelectedWork(work=work, domains={"Chronic Disease", "Behavioral Health"})


def test_dagster_job_and_schedule_are_registered() -> None:
    from ohdp_orchestration.definitions import defs

    rd = defs.get_repository_def()
    assert "literature_trending_sync_job" in {j.name for j in rd.get_all_jobs()}
    schedule = next(
        s for s in rd.schedule_defs if s.name == "literature_trending_sync_schedule"
    )
    assert schedule.job_name == "literature_trending_sync_job"
    assert schedule.default_status.value == "STOPPED"


def test_literature_trending_sync_asset_is_registered() -> None:
    from ohdp_orchestration.definitions import defs

    graph = defs.resolve_asset_graph()
    keys = {k.to_user_string() for k in graph.get_all_asset_keys()}
    assert "openmetadata/literature_trending_sync" in keys


def test_summary_returns_short_abstract_unchanged() -> None:
    assert _summary("Short abstract.") == "Short abstract."


def test_summary_truncates_long_abstract_on_a_word_boundary() -> None:
    long_abstract = "word " * 200
    summary = _summary(long_abstract)
    assert summary.endswith("…")
    assert len(summary) <= _SUMMARY_MAX_CHARS + 1
    assert not summary[:-1].endswith(" ")


def test_trending_item_shape() -> None:
    item = _trending_item(_selected_work())

    assert item["title"] == "A Paper About Something"
    assert item["authors"] == "A. One, B. Two"
    assert item["venue"] == "Journal of Testing"
    assert item["year"] == 2023
    assert item["url"] == "https://doi.org/10.1000/xyz"
    assert item["doi"] == "10.1000/xyz"
    assert item["summary"] == "This is an abstract."
    assert item["tags"] == ["Behavioral Health", "Chronic Disease"]


def test_trending_item_falls_back_to_openalex_id_when_no_title() -> None:
    entry = _selected_work()
    entry.work = OpenAlexWork(
        openalex_id="W999",
        title="",
        doi="",
        pmid="",
        pmcid="",
        cited_by_count=0,
        publication_year=None,
        publication_date=None,
        venue="",
        authors="",
        abstract="",
    )
    item = _trending_item(entry)
    assert item["title"] == "W999"
    assert item["url"] == "https://openalex.org/W999"
