"""OpenAlex client — the citation-ranking backbone for curated literature
ingestion. Free, keyless; a contact email moves requests into OpenAlex's
"polite pool" (a materially higher, more reliable rate limit — see
https://docs.openalex.org/how-to-use-the-api/rate-limits-and-authentication).

OpenAlex ingests PubMed/MEDLINE as one of its underlying feeds (alongside
Crossref, ORCID, institutional repositories), so PubMed-indexed work already
appears here with ``cited_by_count`` attached — see
``ohdp_ingestion.literature.europepmc`` for the MeSH cross-check this module's
results feed into.

Subfield/topic filter values are documented against the live API in
``data/src/ohdp_orchestration/seed/literature_domains.yml`` — this module is a
thin, generic wrapper, not source-specific per domain.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol

import httpx

from ohdp_ingestion.base import http_client
from ohdp_shared import get_logger

log = get_logger(__name__)

_BASE_URL = "https://api.openalex.org"
MAX_PER_PAGE = 200  # OpenAlex's own per_page ceiling


class OpenAlexError(RuntimeError):
    """OpenAlex was unreachable or returned something unusable."""


@dataclass(frozen=True)
class OpenAlexWork:
    """One OpenAlex work, trimmed to what the literature Context Center page needs."""

    openalex_id: str
    title: str
    doi: str
    pmid: str
    pmcid: str
    cited_by_count: int
    publication_year: int | None
    publication_date: date | None
    venue: str
    authors: str
    abstract: str

    @property
    def url(self) -> str:
        """The best link-out target: DOI if present (resolves through the
        publisher), else the OpenAlex landing page itself."""
        if self.doi:
            return f"https://doi.org/{self.doi}"
        return f"https://openalex.org/{self.openalex_id}"


class SupportsTopCitedWorks(Protocol):
    """What ``ohdp_ingestion.literature.selection`` actually needs from an
    OpenAlex client — narrow enough that tests can pass a scripted fake
    instead of the real ``OpenAlexClient`` without a subclass or a
    ``type: ignore``."""

    def top_cited_works(
        self,
        *,
        subfield_id: int,
        limit: int,
        from_publication_year: int | None = None,
        min_citations: int = 0,
    ) -> tuple[OpenAlexWork, ...]: ...


class OpenAlexClient:
    """Works search, ranked by citation count."""

    def __init__(
        self,
        *,
        contact_email: str = "",
        client: httpx.Client | None = None,
    ) -> None:
        self._contact_email = contact_email
        self._client = client or http_client(_BASE_URL)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> OpenAlexClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def top_cited_works(
        self,
        *,
        subfield_id: int,
        limit: int,
        from_publication_year: int | None = None,
        min_citations: int = 0,
    ) -> tuple[OpenAlexWork, ...]:
        """The ``limit`` most-cited works in an OpenAlex subfield, optionally
        restricted to works published on/after ``from_publication_year`` — the
        "recent top-cited" bucket in the selection strategy
        (docs/decisions — curated literature ADR)."""
        filters = [f"topics.subfield.id:{subfield_id}"]
        if from_publication_year is not None:
            filters.append(f"from_publication_date:{from_publication_year}-01-01")
        if min_citations:
            filters.append(f"cited_by_count:>{min_citations - 1}")
        params: dict[str, Any] = {
            "filter": ",".join(filters),
            "sort": "cited_by_count:desc",
            "per_page": min(limit, MAX_PER_PAGE),
        }
        if self._contact_email:
            params["mailto"] = self._contact_email
        response = self._client.get("/works", params=params)
        if response.status_code >= 400:
            raise OpenAlexError(
                f"OpenAlex returned {response.status_code} for subfield {subfield_id}: "
                f"{response.text[:300]}"
            )
        try:
            payload: dict[str, Any] = response.json()
        except ValueError as exc:
            raise OpenAlexError("OpenAlex returned a non-JSON body") from exc
        works = tuple(_parse_work(r) for r in payload.get("results", []) if isinstance(r, dict))
        log.info(
            "openalex_top_cited",
            subfield_id=subfield_id,
            from_publication_year=from_publication_year,
            hits=len(works),
        )
        return works


def _parse_work(result: dict[str, Any]) -> OpenAlexWork:
    ids = result.get("ids", {}) or {}
    primary_location = result.get("primary_location") or {}
    source = primary_location.get("source") or {}
    return OpenAlexWork(
        openalex_id=str(result.get("id", "")).removeprefix("https://openalex.org/"),
        title=str(result.get("title") or result.get("display_name") or ""),
        doi=str(ids.get("doi", "")).removeprefix("https://doi.org/"),
        pmid=str(ids.get("pmid", "")).removeprefix("https://pubmed.ncbi.nlm.nih.gov/"),
        pmcid=str(ids.get("pmcid", "")),
        cited_by_count=int(result.get("cited_by_count") or 0),
        publication_year=result.get("publication_year"),
        publication_date=_parse_date(result.get("publication_date")),
        venue=str(source.get("display_name", "")),
        authors=_author_string(result),
        abstract=_reconstruct_abstract(result.get("abstract_inverted_index")),
    )


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)).date()
    except ValueError:
        return None


def _author_string(result: dict[str, Any]) -> str:
    names: list[str] = []
    for authorship in result.get("authorships", []) or []:
        author = authorship.get("author") or {}
        name = author.get("display_name")
        if name:
            names.append(str(name))
    return ", ".join(names)


def _reconstruct_abstract(inverted_index: dict[str, list[int]] | None) -> str:
    """OpenAlex ships abstracts as a word -> position inverted index (a
    copyright-driven workaround baked into the API, not something to route
    around) — this rebuilds the plain text from it."""
    if not inverted_index:
        return ""
    positions: dict[int, str] = {}
    for word, idxs in inverted_index.items():
        for idx in idxs:
            positions[idx] = word
    return " ".join(positions[i] for i in sorted(positions))
