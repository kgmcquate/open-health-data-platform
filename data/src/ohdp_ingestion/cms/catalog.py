"""Walk CMS's Project Open Data catalog (``data.cms.gov/data.json``).

CMS is *not* Socrata or CKAN (ADR-0026 corrects ADR-0018's guess) — it
publishes its own ``data-api/v1`` REST API, discoverable through the federal
Project Open Data / DCAT-US catalog every ``.gov`` agency is required to
publish at ``/data.json`` (the same metadata schema Socrata's own
``Common-Core_*`` catalog fields are drawn from, which is why the cadence
mapping below is identical to :mod:`ohdp_ingestion.socrata.catalog`'s).

One catalog entry is a dataset *series* — e.g. "Medicare Part D Spending by
Drug" — republished on a cadence, each vintage its own ``distribution`` with
its own UUID. Only the ``distribution`` tagged ``"description": "latest"``
(and of ``"format": "API"``) is live data; older vintages are archival CSVs
and are skipped entirely. About 130 of CMS's ~160 series currently have one;
the rest publish CSV-only and are skipped (nothing to ingest via the API).

Reference: https://data.cms.gov/api-docs, https://resources.data.gov/schemas/dcat-us/v1.1/
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from ohdp_ingestion.base import http_client
from ohdp_shared import get_logger

log = get_logger(__name__)

CMS_BASE_URL = "https://data.cms.gov"
_CATALOG_PATH = "/data.json"
_STATS_PATH = "/data-api/v1/dataset/{uuid}/data-viewer/stats"

# Same ISO-8601 recurring-interval vocabulary Socrata's Common-Core_Update-
# Frequency uses (both are Project Open Data / DCAT-US), so the mapping to our
# three schedule buckets is identical — see
# ohdp_ingestion.socrata.catalog._CADENCE_BY_FREQUENCY for the same table.
_CADENCE_BY_FREQUENCY = {
    "R/PT1H": "daily",
    "R/PT1S": "daily",
    "R/P1D": "daily",
    "R/P2D": "daily",
    "R/P3.5D": "daily",
    "R/P0.33W": "daily",
    "R/P0.5W": "weekly",
    "R/P1W": "weekly",
    "R/P2W": "weekly",
    "R/P7D": "weekly",
    "R/P0.5M": "weekly",
    "R/P1M": "monthly",
    "R/P3M": "monthly",
    "R/P4M": "monthly",
    "R/P6M": "monthly",
    "R/P1Y": "monthly",
    "R/P2Y": "monthly",
    "R/P4Y": "monthly",
}
_DEFAULT_CADENCE = "weekly"

_UUID_RE = re.compile(r"/dataset/([0-9a-f-]{36})/data")


@dataclass(slots=True)
class CatalogDataset:
    """One CMS dataset series, normalized down to what a config needs."""

    id: str
    """UUID of the series' *latest* API distribution — stable across
    republishes of the same series (a new vintage gets a new UUID, but the
    ``data.json`` entry's ``identifier`` always points at whichever one is
    current)."""

    name: str
    description: str
    publisher: str
    landing_page: str
    modified: str | None
    update_frequency: str | None
    keywords: list[str] = field(default_factory=list)
    row_count: int = 0

    @property
    def cadence(self) -> str:
        return _CADENCE_BY_FREQUENCY.get(self.update_frequency or "", _DEFAULT_CADENCE)


def _latest_api_distribution(entry: dict[str, Any]) -> dict[str, Any] | None:
    for dist in entry.get("distribution") or []:
        if dist.get("format") == "API" and dist.get("description") == "latest":
            return dist
    return None


def _uuid_from_access_url(access_url: str) -> str | None:
    match = _UUID_RE.search(access_url)
    return match.group(1) if match else None


def _row_count(client: Any, uuid: str) -> int:
    """Best-effort total row count via the dataset's stats endpoint — used for
    display (README tables, picking an initial enabled set) only, never to
    drive ingestion, so a failure here is not fatal to the scrape."""
    try:
        resp = client.get(_STATS_PATH.format(uuid=uuid))
        resp.raise_for_status()
        return int(resp.json().get("data", {}).get("total_rows") or 0)
    except Exception:  # noqa: BLE001 — informational only
        log.warning("cms: could not fetch row count for %s", uuid)
        return 0


def iter_catalog(
    *, with_row_counts: bool = True, limit: int | None = None
) -> Iterator[CatalogDataset]:
    """Yield every CMS dataset series that has a live API distribution.

    ``with_row_counts`` fetches each dataset's ``data-viewer/stats`` endpoint
    (one extra request per dataset) to populate ``row_count``; set it to
    ``False`` for a fast dry run.
    """
    with http_client(CMS_BASE_URL) as client:
        resp = client.get(_CATALOG_PATH)
        resp.raise_for_status()
        entries = resp.json().get("dataset", [])

        yielded = 0
        for entry in entries:
            dist = _latest_api_distribution(entry)
            if dist is None:
                continue
            uuid = _uuid_from_access_url(dist.get("accessURL", ""))
            if not uuid:
                continue

            publisher = (entry.get("publisher") or {}).get("name", "")
            row_count = _row_count(client, uuid) if with_row_counts else 0

            yield CatalogDataset(
                id=uuid,
                name=entry.get("title") or uuid,
                description=entry.get("description") or "",
                publisher=publisher,
                landing_page=entry.get("landingPage") or f"{CMS_BASE_URL}/dataset/{uuid}",
                modified=dist.get("modified") or entry.get("modified"),
                update_frequency=entry.get("accrualPeriodicity"),
                keywords=list(entry.get("keyword") or [])[:12],
                row_count=row_count,
            )
            yielded += 1
            if limit is not None and yielded >= limit:
                return
