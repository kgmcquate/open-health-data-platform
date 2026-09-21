"""Combines OpenAlex's recent-citation ranking with a Europe PMC/MeSH
cross-check into the "trending per Domain" feed, driven entirely by
``data/src/ohdp_orchestration/seed/literature_domains.yml``.

Kept as a pure function of (config, OpenAlex client) -> selected works so it can
be exercised standalone without a Dagster context or an OpenMetadata
connection.

**Recent-only, deliberately.** This used to also rank an all-time top-cited
bucket per subfield and merge it into the same corpus (feeding a since-removed
domain-wide Context Center sync — see
``ohdp_orchestration.assets.literature_dataset_sync`` for what replaced it).
That bucket is dropped here: an OpenAlex subfield's all-time most-cited works
skew hard toward decades-old methods/reagent/software papers (a stats package,
an assay protocol) rather than anything a "trending" reader means by the word,
and this selection's only consumer now
(``ohdp_orchestration.assets.literature_trending_sync``) is explicitly a
trending feed, not a canonical one. Recency + citation count together is a
reasonable trending proxy; recency alone is not (a fresh paper has had no time
to accumulate citations) and citation count alone is the discredited bucket
this replaced.
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
    """One Domain's trending-ranking config, one entry of
    ``literature_domains.yml``."""

    domain: str
    openalex_subfields: tuple[int, ...]
    mesh_terms: frozenset[str]
    limit: int
    recent_years: int
    min_citations: int


@dataclass
class SelectedWork:
    """One paper selected as trending for at least one Domain — carries every
    Domain it matched, so a work trending in more than one Domain becomes one
    row, tagged with all of them, not a duplicate per Domain."""

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
            limit=entry.get("limit", defaults["limit"]),
            recent_years=entry.get("recent_years", defaults["recent_years"]),
            min_citations=entry.get("min_citations", defaults["min_citations"]),
        )
        for entry in spec["domains"]
    )


def select_trending_literature(
    selections: tuple[DomainSelection, ...],
    *,
    openalex: SupportsTopCitedWorks,
    current_year: int,
    contact_email: str = "",
) -> dict[str, SelectedWork]:
    """Runs the recent-top-cited OpenAlex ranking per Domain/subfield,
    cross-checks against MeSH where the Domain configures it, and returns the
    deduped trending corpus keyed by OpenAlex work ID."""
    selected: dict[str, SelectedWork] = {}
    for cfg in selections:
        candidates: dict[str, OpenAlexWork] = {}
        for subfield_id in cfg.openalex_subfields:
            for work in openalex.top_cited_works(
                subfield_id=subfield_id,
                limit=cfg.limit,
                from_publication_year=current_year - cfg.recent_years,
                min_citations=cfg.min_citations,
            ):
                candidates[work.openalex_id] = work

        confirmed = _mesh_confirm(candidates, cfg.mesh_terms, contact_email=contact_email)
        log.info(
            "literature_domain_trending_selected",
            domain=cfg.domain,
            candidates=len(candidates),
            confirmed=len(confirmed),
        )
        for work in confirmed:
            entry = selected.setdefault(work.openalex_id, SelectedWork(work=work))
            entry.domains.add(cfg.domain)
    return selected


def _mesh_confirm(
    candidates: dict[str, OpenAlexWork], mesh_terms: frozenset[str], *, contact_email: str = ""
) -> list[OpenAlexWork]:
    """No MeSH terms configured for the Domain, or a candidate with no PMID:
    keep it, nothing to cross-check against. Otherwise drop anything whose
    Europe PMC MeSH headings don't intersect ``mesh_terms``."""
    if not mesh_terms:
        return list(candidates.values())

    pmids = [w.pmid for w in candidates.values() if w.pmid]
    headings = mesh_headings_for_pmids(pmids, contact_email=contact_email)

    kept: list[OpenAlexWork] = []
    for work in candidates.values():
        # None means no PMID or no Europe PMC record (yet) — nothing to
        # cross-check, so keep it rather than penalizing missing data.
        work_headings = headings.get(work.pmid)
        if work_headings is None or work_headings & mesh_terms:
            kept.append(work)
    return kept
