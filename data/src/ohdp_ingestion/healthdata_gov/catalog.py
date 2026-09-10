"""Walk the Socrata catalog API for one domain.

The catalog API is paginated, cheap, and returns everything the scraper needs to
write a dataset config without ever fetching a row: the 4x4 id, the column
schema, the declared update cadence, and popularity counters.

Reference: https://socratadiscovery.docs.apiary.io/
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from ohdp_ingestion.base import http_client
from ohdp_shared import get_logger

log = get_logger(__name__)

CATALOG_BASE_URL = "https://api.us.socrata.com"
_CATALOG_PATH = "/api/catalog/v1"
_PAGE = 100

# ISO 8601 recurring-interval codes Socrata uses in Common-Core_Update-Frequency,
# mapped to our three schedule buckets. Anything unmatched falls back to _DEFAULT.
_CADENCE_BY_FREQUENCY = {
    "R/PT1H": "daily",
    "R/PT1S": "daily",
    "R/P1D": "daily",
    "R/P2D": "daily",
    "R/P0.33W": "daily",
    "R/P0.5W": "weekly",
    "R/P1W": "weekly",
    "R/P7D": "weekly",
    "R/P2W": "weekly",
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

# Socrata column datatypes we can hand to a date-based incremental cursor.
_DATE_TYPES = {"calendar_date", "date", "floating_timestamp", "fixed_timestamp"}
# Field-name hints, best first, for picking the incremental cursor column.
_DATE_NAME_HINTS = (
    "week_ending",
    "report_date",
    "reporting_date",
    "collection_date",
    "submission_date",
    "date_updated",
    "last_updated",
    "last_report_date",
    "as_of_date",
    "data_as_of",
    "week_end",
    "date",
)

_TAG_RE = re.compile(r"<[^>]+>")


def _clean(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", _TAG_RE.sub(" ", text)).strip()


@dataclass(slots=True)
class CatalogColumn:
    name: str
    datatype: str
    description: str = ""


@dataclass(slots=True)
class CatalogDataset:
    """One Socrata dataset, normalized down to what a config needs."""

    id: str
    name: str
    description: str
    publisher: str
    landing_page: str
    updated_at: str | None
    row_type: str
    page_views_total: int
    download_count: int
    update_frequency: str | None
    keywords: list[str] = field(default_factory=list)
    columns: list[CatalogColumn] = field(default_factory=list)

    @property
    def cadence(self) -> str:
        return _CADENCE_BY_FREQUENCY.get(self.update_frequency or "", _DEFAULT_CADENCE)

    def incremental_field(self) -> str | None:
        """Best guess at a monotonic date column to use as the dlt cursor.

        Returns ``None`` when nothing looks safe — the caller then does a full
        replace on every run, which is correct (if wasteful) for any dataset.
        """
        dated = [c for c in self.columns if c.datatype in _DATE_TYPES]
        if not dated:
            return None
        for hint in _DATE_NAME_HINTS:
            for col in dated:
                if hint in col.name:
                    return col.name
        return None


def _metadata_value(meta: list[dict[str, str]], key: str) -> str | None:
    for entry in meta:
        if entry.get("key") == key:
            return entry.get("value")
    return None


def _parse_result(result: dict[str, Any]) -> CatalogDataset | None:
    resource = result.get("resource", {})
    if resource.get("type") not in ("dataset", "map", "chart", "filter"):
        return None
    dataset_id = resource.get("id")
    if not dataset_id:
        return None

    classification = result.get("classification", {})
    meta = classification.get("domain_metadata", [])
    publisher = (
        _metadata_value(meta, "Common-Core_Publisher")
        or classification.get("domain_category")
        or (result.get("owner") or {}).get("display_name")
        or "healthdata.gov"
    )

    names = resource.get("columns_field_name", []) or []
    types = resource.get("columns_datatype", []) or []
    descs = resource.get("columns_description", []) or []
    columns = [
        CatalogColumn(
            name=n,
            datatype=(types[i] if i < len(types) else "").lower().replace(" ", "_"),
            description=_clean(descs[i] if i < len(descs) else ""),
        )
        for i, n in enumerate(names)
    ]

    page_views = resource.get("page_views", {}) or {}

    return CatalogDataset(
        id=dataset_id,
        name=_clean(resource.get("name")) or dataset_id,
        description=_clean(resource.get("description")),
        publisher=_clean(publisher),
        landing_page=result.get("permalink") or f"https://healthdata.gov/d/{dataset_id}",
        updated_at=resource.get("updatedAt") or resource.get("data_updated_at"),
        row_type=resource.get("type", "dataset"),
        page_views_total=int(page_views.get("page_views_total") or 0),
        download_count=int(resource.get("download_count") or 0),
        update_frequency=_metadata_value(meta, "Common-Core_Update-Frequency"),
        keywords=[t for t in (classification.get("domain_tags") or []) if t][:12],
        columns=columns,
    )


def iter_catalog(
    domain: str = "healthdata.gov",
    *,
    only: str = "datasets",
    limit: int | None = None,
) -> Iterator[CatalogDataset]:
    """Yield every catalog entry for ``domain``, newest change first.

    ``limit`` caps the number yielded (handy for a dry run); ``None`` walks the
    whole catalog.
    """
    offset = 0
    yielded = 0
    with http_client(CATALOG_BASE_URL) as client:
        while True:
            params: dict[str, str | int] = {
                "domains": domain,
                "search_context": domain,
                "only": only,
                "order": "updatedAt",
                "limit": _PAGE,
                "offset": offset,
            }
            resp = client.get(_CATALOG_PATH, params=params)
            resp.raise_for_status()
            results = resp.json().get("results", [])
            if not results:
                return
            for result in results:
                parsed = _parse_result(result)
                if parsed is None:
                    continue
                yield parsed
                yielded += 1
                if limit is not None and yielded >= limit:
                    return
            offset += _PAGE
