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

import time
from typing import Any

import httpx

from ohdp_ingestion.base import http_client
from ohdp_shared import get_logger

log = get_logger(__name__)

_SEARCH_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
_BATCH_SIZE = 25  # Europe PMC's practical page-size ceiling for a polite client
_MAX_ATTEMPTS = 4
_RETRY_BACKOFF_SECONDS = 2.0  # doubles each attempt: 2s, 4s, 8s


def _user_agent(contact_email: str) -> str:
    """Same self-identification convention as ``openalex.py``'s ``mailto``
    param — Europe PMC has no equivalent query param, so the contact address
    goes in the User-Agent instead."""
    base = "ohdp-ingestion/1.0 (literature MeSH cross-check)"
    return f"{base} contact:{contact_email}" if contact_email else base


class EuropePMCError(RuntimeError):
    """Europe PMC was unreachable or returned something unusable."""


def mesh_headings_for_pmids(
    pmids: list[str],
    *,
    contact_email: str = "",
    client: httpx.Client | None = None,
    timeout: float = 20.0,
) -> dict[str, frozenset[str]]:
    """MeSH descriptor names per PMID, for every PMID Europe PMC has an indexed
    record for. A PMID missing from the result carries no MeSH data (not yet
    indexed, or not a PubMed record at all) — callers should treat that as
    "no cross-check available," not "domain rejected."

    ``contact_email`` self-identifies the client in the User-Agent (Europe
    PMC's equivalent of OpenAlex's polite-pool ``mailto`` — see
    ``openalex.py``); ignored when ``client`` is supplied, since the caller
    owns that client's headers.
    """
    if not pmids:
        return {}
    owns_client = client is None
    http = client or http_client(
        "https://www.ebi.ac.uk", headers={"User-Agent": _user_agent(contact_email)}
    )
    headings: dict[str, frozenset[str]] = {}
    try:
        for batch_start in range(0, len(pmids), _BATCH_SIZE):
            batch = pmids[batch_start : batch_start + _BATCH_SIZE]
            query = " OR ".join(f"EXT_ID:{pmid}" for pmid in batch)
            response = _get_with_retry(http, query, timeout=timeout)
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


def _get_with_retry(http: httpx.Client, query: str, *, timeout: float) -> httpx.Response:
    """Europe PMC's search endpoint occasionally stalls on a slow query under
    load; a bare timeout there shouldn't fail the whole literature sync. Retries
    only network-level failures (timeout/connect) — a 4xx/5xx response is
    returned as-is for the caller to raise on, since retrying those wouldn't
    help."""
    last_exc: httpx.TransportError | None = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            return http.get(
                _SEARCH_URL,
                params={
                    "query": query,
                    "format": "json",
                    "resultType": "core",
                    "pageSize": _BATCH_SIZE,
                },
                timeout=timeout,
            )
        except httpx.TransportError as exc:
            last_exc = exc
            if attempt == _MAX_ATTEMPTS - 1:
                break
            delay = _RETRY_BACKOFF_SECONDS * (2**attempt)
            log.warning(
                "europepmc_request_retry",
                attempt=attempt + 1,
                max_attempts=_MAX_ATTEMPTS,
                delay_seconds=delay,
                error=str(exc),
            )
            time.sleep(delay)
    raise EuropePMCError(f"Europe PMC request failed after {_MAX_ATTEMPTS} attempts") from last_exc


def _mesh_terms(result: dict[str, Any]) -> frozenset[str]:
    mesh_list = result.get("meshHeadingList") or {}
    headings = mesh_list.get("meshHeading") or []
    return frozenset(
        str(h["descriptorName"])
        for h in headings
        if isinstance(h, dict) and h.get("descriptorName")
    )
