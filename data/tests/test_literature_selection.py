"""Offline checks for `ohdp_ingestion.literature.selection`'s pure ranking/
cross-check/dedup logic. Nothing here hits the network — OpenAlex is a fake
with a scripted `top_cited_works`, and MeSH lookups are monkeypatched."""

from __future__ import annotations

from datetime import date

import pytest

from ohdp_ingestion.literature.openalex import OpenAlexWork
from ohdp_ingestion.literature.selection import (
    DomainSelection,
    _mesh_confirm,
    select_trending_literature,
)


def _work(openalex_id: str, *, pmid: str = "", cited_by_count: int = 100) -> OpenAlexWork:
    return OpenAlexWork(
        openalex_id=openalex_id,
        title=f"Paper {openalex_id}",
        doi="",
        pmid=pmid,
        pmcid="",
        cited_by_count=cited_by_count,
        publication_year=2024,
        publication_date=date(2024, 1, 1),
        venue="",
        authors="",
        abstract="",
    )


class _FakeOpenAlexClient:
    """Scripted `top_cited_works` — one fixed set of results regardless of
    which subfield/window is asked for, which is all
    `select_trending_literature`'s dedup-by-work-id logic needs to be
    exercised."""

    def __init__(self, works: tuple[OpenAlexWork, ...]) -> None:
        self._works = works

    def top_cited_works(self, **_kwargs: object) -> tuple[OpenAlexWork, ...]:
        return self._works


def test_mesh_confirm_keeps_everything_when_no_mesh_terms_configured() -> None:
    candidates = {"W1": _work("W1", pmid="111")}
    assert _mesh_confirm(candidates, frozenset()) == [candidates["W1"]]


def test_mesh_confirm_keeps_works_with_no_pmid(monkeypatch: pytest.MonkeyPatch) -> None:
    candidates = {"W1": _work("W1", pmid="")}
    monkeypatch.setattr(
        "ohdp_ingestion.literature.selection.mesh_headings_for_pmids", lambda pmids, **_kwargs: {}
    )
    kept = _mesh_confirm(candidates, frozenset({"Child Welfare"}))
    assert kept == [candidates["W1"]]


def test_mesh_confirm_drops_works_whose_mesh_headings_dont_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidates = {
        "W1": _work("W1", pmid="111"),  # matches
        "W2": _work("W2", pmid="222"),  # doesn't match
    }
    monkeypatch.setattr(
        "ohdp_ingestion.literature.selection.mesh_headings_for_pmids",
        lambda pmids, **_kwargs: {
            "111": frozenset({"Child Welfare", "Pediatrics"}),
            "222": frozenset({"Oncology"}),
        },
    )
    kept = _mesh_confirm(candidates, frozenset({"Child Welfare"}))
    assert kept == [candidates["W1"]]


def test_select_trending_literature_dedupes_and_unions_domains() -> None:
    shared_work = _work("W_SHARED")
    only_a = _work("W_A_ONLY")

    client_a = _FakeOpenAlexClient((shared_work, only_a))
    client_b = _FakeOpenAlexClient((shared_work,))

    class _RoutingClient:
        """Returns domain A's fixture for the first call and domain B's
        fixture after that."""

        def __init__(self) -> None:
            self._calls = 0

        def top_cited_works(self, **kwargs: object) -> tuple[OpenAlexWork, ...]:
            self._calls += 1
            source = client_a if self._calls == 1 else client_b
            return source.top_cited_works(**kwargs)

    selections = (
        DomainSelection(
            domain="Domain A",
            openalex_subfields=(1,),
            mesh_terms=frozenset(),
            limit=5,
            recent_years=2,
            min_citations=0,
        ),
        DomainSelection(
            domain="Domain B",
            openalex_subfields=(2,),
            mesh_terms=frozenset(),
            limit=5,
            recent_years=2,
            min_citations=0,
        ),
    )

    selected = select_trending_literature(selections, openalex=_RoutingClient(), current_year=2026)

    assert set(selected) == {"W_SHARED", "W_A_ONLY"}
    assert selected["W_SHARED"].domains == {"Domain A", "Domain B"}
    assert selected["W_A_ONLY"].domains == {"Domain A"}
