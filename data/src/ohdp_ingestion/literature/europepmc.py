"""Europe PMC MeSH lookup — the domain cross-check for OpenAlex-ranked works.

OpenAlex's subfield taxonomy gets the citation ranking right but is a coarse,
machine-derived classification; PubMed's MeSH controlled vocabulary is curated
by human indexers and several platform Domains have a literal MeSH heading
match ("Child Welfare", "Immunization"). This module fetches MeSH headings for
a batch of PMIDs so ``ohdp_ingestion.literature.selection`` can keep only the
works whose indexing actually confirms the domain, not just OpenAlex's subfield
guess.

Deliberately not the ``LiteratureClient`` in ``apps/api/src/ohdp_agent`` — that
package is a separate deployable (hub-api), not importable from ``data/``'s own
venv/image, and this module only needs one read-only endpoint. Same underlying
API (Europe PMC's keyless REST search), used the same polite way.
"""

from __future__ import annotations

from typing import Any

import httpx

from ohdp_ingestion.base import http_client
from ohdp_shared import get_logger

log = get_logger(__name__)

_SEARCH_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
_BATCH_SIZE = 25  # Europe PMC's practical page-size ceiling for a polite client


class EuropePMCError(RuntimeError):
    """Europe PMC was unreachable or returned something unusable."""


def mesh_headings_for_pmids(
    pmids: list[str],
    *,
    client: httpx.Client | None = None,
    timeout: float = 20.0,
) -> dict[str, frozenset[str]]:
    """MeSH descriptor names per PMID, for every PMID Europe PMC has an indexed
    record for. A PMID missing from the result carries no MeSH data (not yet
    indexed, or not a PubMed record at all) — callers should treat that as
    "no cross-check available," not "domain rejected."
    """
    if not pmids:
        return {}
    owns_client = client is None
    http = client or http_client("https://www.ebi.ac.uk")
    headings: dict[str, frozenset[str]] = {}
    try:
        for batch_start in range(0, len(pmids), _BATCH_SIZE):
            batch = pmids[batch_start : batch_start + _BATCH_SIZE]
            query = " OR ".join(f"EXT_ID:{pmid}" for pmid in batch)
            response = http.get(
                _SEARCH_URL,
                params={
                    "query": query,
                    "format": "json",
                    "resultType": "core",
                    "pageSize": _BATCH_SIZE,
                },
                timeout=timeout,
            )
            if response.status_code >= 400:
                raise EuropePMCError(f"Europe PMC returned {response.status_code}")
            try:
                payload: dict[str, Any] = response.json()
            except ValueError as exc:
                raise EuropePMCError("Europe PMC returned a non-JSON body") from exc
            for result in payload.get("resultList", {}).get("result", []):
                if not isinstance(result, dict):
                    continue
                pmid = str(result.get("pmid", ""))
                if not pmid:
                    continue
                headings[pmid] = _mesh_terms(result)
        log.info("europepmc_mesh_lookup", requested=len(pmids), resolved=len(headings))
        return headings
    finally:
        if owns_client:
            http.close()


def _mesh_terms(result: dict[str, Any]) -> frozenset[str]:
    mesh_list = result.get("meshHeadingList") or {}
    headings = mesh_list.get("meshHeading") or []
    return frozenset(
        str(h["descriptorName"])
        for h in headings
        if isinstance(h, dict) and h.get("descriptorName")
    )
