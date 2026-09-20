"""Combines OpenAlex citation ranking with a Europe PMC/MeSH cross-check into
the final, deduped literature corpus, driven entirely by
``data/src/ohdp_orchestration/seed/literature_domains.yml``.

Kept as a pure function of (config, OpenAlex client) -> selected works so it can
be exercised standalone (see the plan's verification step 1) without a Dagster
context or an OpenMetadata connection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ohdp_ingestion.literature.europepmc import mesh_headings_for_pmids
from ohdp_ingestion.literature.openalex import OpenAlexWork, SupportsTopCitedWorks
from ohdp_shared import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class DomainSelection:
    """One Domain's ranking config, one entry of ``literature_domains.yml``."""

    domain: str
    openalex_subfields: tuple[int, ...]
    mesh_terms: frozenset[str]
    top_cited_limit: int
    recent_limit: int
    recent_years: int
    min_citations: int


@dataclass
class SelectedWork:
    """One paper selected for at least one Domain — carries every Domain it
    matched, so a work relevant to more than one Domain becomes one Context
    Center page tagged with all of them, not a duplicate per Domain."""

    work: OpenAlexWork
    domains: set[str] = field(default_factory=set)


def load_domain_selections(seed_path: Path) -> tuple[DomainSelection, ...]:
    spec: dict[str, Any] = yaml.safe_load(seed_path.read_text())
    defaults = spec.get("defaults", {})
    return tuple(
        DomainSelection(
            domain=entry["domain"],
            openalex_subfields=tuple(entry["openalex_subfields"]),
            mesh_terms=frozenset(entry.get("mesh_terms", [])),
            top_cited_limit=entry.get("top_cited_limit", defaults["top_cited_limit"]),
            recent_limit=entry.get("recent_limit", defaults["recent_limit"]),
            recent_years=entry.get("recent_years", defaults["recent_years"]),
            min_citations=entry.get("min_citations", defaults["min_citations"]),
        )
        for entry in spec["domains"]
    )


def select_literature(
    selections: tuple[DomainSelection, ...],
    *,
    openalex: SupportsTopCitedWorks,
    current_year: int,
) -> dict[str, SelectedWork]:
    """Runs the two-bucket OpenAlex ranking (all-time top-cited + recent
    top-cited) per Domain/subfield, cross-checks against MeSH where the Domain
    configures it, and returns the deduped corpus keyed by OpenAlex work ID."""
    selected: dict[str, SelectedWork] = {}
    for cfg in selections:
        candidates: dict[str, OpenAlexWork] = {}
        for subfield_id in cfg.openalex_subfields:
            for work in openalex.top_cited_works(
                subfield_id=subfield_id,
                limit=cfg.top_cited_limit,
                min_citations=cfg.min_citations,
            ):
                candidates[work.openalex_id] = work
            for work in openalex.top_cited_works(
                subfield_id=subfield_id,
                limit=cfg.recent_limit,
                from_publication_year=current_year - cfg.recent_years,
                min_citations=cfg.min_citations,
            ):
                candidates[work.openalex_id] = work

        confirmed = _mesh_confirm(candidates, cfg.mesh_terms)
        log.info(
            "literature_domain_selected",
            domain=cfg.domain,
            candidates=len(candidates),
            confirmed=len(confirmed),
        )
        for work in confirmed:
            entry = selected.setdefault(work.openalex_id, SelectedWork(work=work))
            entry.domains.add(cfg.domain)
    return selected


def _mesh_confirm(
    candidates: dict[str, OpenAlexWork], mesh_terms: frozenset[str]
) -> list[OpenAlexWork]:
    """No MeSH terms configured for the Domain, or a candidate with no PMID:
    keep it, nothing to cross-check against. Otherwise drop anything whose
    Europe PMC MeSH headings don't intersect ``mesh_terms``."""
    if not mesh_terms:
        return list(candidates.values())

    pmids = [w.pmid for w in candidates.values() if w.pmid]
    headings = mesh_headings_for_pmids(pmids)

    kept: list[OpenAlexWork] = []
    for work in candidates.values():
        # None means no PMID or no Europe PMC record (yet) — nothing to
        # cross-check, so keep it rather than penalizing missing data.
        work_headings = headings.get(work.pmid)
        if work_headings is None or work_headings & mesh_terms:
            kept.append(work)
    return kept
