"""Literature search over Europe PMC (docs/chatbot.md §2.3).

Europe PMC's REST search is free, keyless, covers PubMed plus preprints, and
returns structured records with DOI/PMID — strictly better than E-utilities for
this. It is what serves persona P2, who starts outside the warehouse.

The rule this module exists to enforce: **the agent may cite only identifiers a
tool actually returned.** A fabricated citation is worse than no answer for a
researcher, and it is the one hallucination class that reliably survives a
confident-sounding paragraph. So every result is registered on the way out
(`CitationRegistry`), and the final response is checked against that registry
before it reaches the user (`unverified_citations`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from ohdp_shared import get_logger

log = get_logger(__name__)

SEARCH_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
MAX_PAGE_SIZE = 25

# Identifier shapes we recognise in generated prose. Deliberately broad: a false
# positive costs one registry lookup, a false negative lets a fake citation out.
_PMID_RE = re.compile(r"\bPMID:?\s*(\d{1,8})\b", re.IGNORECASE)
_PMCID_RE = re.compile(r"\b(PMC\d{6,9})\b", re.IGNORECASE)
_DOI_RE = re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)", re.IGNORECASE)


class LiteratureError(RuntimeError):
    """Europe PMC was unreachable or returned something unusable."""


@dataclass(frozen=True)
class Article:
    """One Europe PMC record, trimmed to what the agent should reason over."""

    pmid: str
    pmcid: str
    doi: str
    title: str
    authors: str
    journal: str
    year: str
    abstract: str

    def identifiers(self) -> set[str]:
        """Every form this article may legitimately be cited by, normalised."""
        return {_normalise(v) for v in (self.pmid, self.pmcid, self.doi) if v}


@dataclass
class CitationRegistry:
    """Identifiers returned by literature tools during one conversation.

    Mutable and conversation-scoped: hub-api creates one per chat session and
    passes it to every literature call, then checks the final answer against it.
    """

    seen: set[str] = field(default_factory=set)

    def register(self, articles: tuple[Article, ...]) -> None:
        for article in articles:
            self.seen |= article.identifiers()

    def knows(self, identifier: str) -> bool:
        return _normalise(identifier) in self.seen


def _normalise(identifier: str) -> str:
    return identifier.strip().lower().removeprefix("pmid:").strip()


def unverified_citations(text: str, registry: CitationRegistry) -> list[str]:
    """Identifiers cited in `text` that no tool ever returned.

    A non-empty result means the model invented a reference. Callers should strip
    the offending citations and flag the answer rather than shipping it — see
    docs/chatbot.md §6.
    """
    found: list[str] = []
    for match in _PMID_RE.finditer(text):
        found.append(match.group(1))
    for match in _PMCID_RE.finditer(text):
        found.append(match.group(1))
    for match in _DOI_RE.finditer(text):
        # Trailing punctuation is part of the sentence, not the DOI.
        found.append(match.group(1).rstrip(".,;)"))

    unverified = [ident for ident in found if not registry.knows(ident)]
    if unverified:
        log.warning("unverified_citations", count=len(unverified))
    return unverified


class LiteratureClient:
    """Europe PMC search. No API key; be polite with page sizes."""

    def __init__(
        self,
        *,
        timeout: float = 20.0,
        registry: CitationRegistry | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._timeout = timeout
        self.registry = registry if registry is not None else CitationRegistry()
        self._client = client

    async def _get(self, params: dict[str, str | int]) -> dict[str, Any]:
        if self._client is not None:
            response = await self._client.get(SEARCH_URL, params=params)
        else:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(SEARCH_URL, params=params)
        if response.status_code >= 400:
            raise LiteratureError(f"Europe PMC returned {response.status_code}")
        try:
            payload: dict[str, Any] = response.json()
        except ValueError as exc:
            raise LiteratureError("Europe PMC returned a non-JSON body") from exc
        return payload

    async def search_literature(self, query: str, *, limit: int = 10) -> tuple[Article, ...]:
        """Search titles and abstracts. Registers every hit as citable."""
        if not query.strip():
            raise LiteratureError("empty query")
        payload = await self._get(
            {
                "query": query,
                "format": "json",
                "resultType": "core",
                "pageSize": min(limit, MAX_PAGE_SIZE),
            }
        )
        articles = _parse_results(payload)
        self.registry.register(articles)
        log.info("literature_searched", hits=len(articles))
        return articles

    async def get_article(self, identifier: str) -> Article:
        """Fetch one record by PMID, PMCID or DOI."""
        articles = await self.search_literature(_as_query(identifier), limit=1)
        if not articles:
            raise LiteratureError(f"No Europe PMC record for {identifier!r}")
        return articles[0]


def _as_query(identifier: str) -> str:
    ident = identifier.strip()
    if ident.lower().startswith("pmc"):
        return f"PMCID:{ident}"
    if ident.startswith("10."):
        return f"DOI:{ident}"
    return f"EXT_ID:{ident}"


def _journal_title(result: dict[str, Any]) -> str:
    """`resultType=core` nests the journal; there is no top-level `journalTitle`.

    The lite result type does expose one, so fall back to it rather than
    assuming every caller asked for core.
    """
    info = result.get("journalInfo")
    if isinstance(info, dict):
        journal = info.get("journal")
        if isinstance(journal, dict):
            title = journal.get("title") or journal.get("medlineAbbreviation")
            if title:
                return str(title)
    return str(result.get("journalTitle", ""))


def _parse_results(payload: dict[str, Any]) -> tuple[Article, ...]:
    results = payload.get("resultList", {}).get("result", [])
    if not isinstance(results, list):
        return ()
    return tuple(
        Article(
            pmid=str(r.get("pmid", "")),
            pmcid=str(r.get("pmcid", "")),
            doi=str(r.get("doi", "")),
            title=str(r.get("title", "")),
            authors=str(r.get("authorString", "")),
            journal=_journal_title(r),
            year=str(r.get("pubYear", "")),
            abstract=str(r.get("abstractText", "")),
        )
        for r in results
        if isinstance(r, dict)
    )
