"""The per-dataset config contract for openFDA ingestion.

One YAML file lives at ``ohdp_orchestration/defs/openfda/datasets/defs.yaml``,
hand-maintained (not scraped — see the package docstring for why) and read by
the Dagster component (``ohdp_orchestration.defs.openfda.component.OpenFDADataset``).

No ``id``/``incremental_cursor``/``ColumnSpec`` here, unlike Socrata: an
openFDA "dataset" isn't a catalog entry with per-row system columns to page
against, it's just which of openFDA's fixed REST endpoints
(``drug/enforcement``, ``food/enforcement``, ...) this instance pulls,
optionally narrowed by ``search`` — see ``ohdp_ingestion.cms.config`` /
``ohdp_ingestion.openaq.config`` for the same reasoning applied to CMS/OpenAQ.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ohdp_ingestion.cadence import CADENCES, Cadence

__all__ = ["CADENCES", "MAX_SKIP", "Cadence", "DatasetConfig"]

# openFDA's own documented ceiling on `skip` — paging past this requires
# sorting by a unique field and switching to `search_after`, which
# ohdp_ingestion.openfda.source doesn't implement. Every instance's effective
# row_limit is capped at this regardless of what's configured below.
MAX_SKIP = 25_000


class DatasetConfig(BaseModel):
    """One openFDA REST endpoint to ingest, e.g. ``drug/enforcement``."""

    model_config = {"extra": "forbid"}

    name: str
    publisher: str = "U.S. Food and Drug Administration"
    description: str = ""
    source_url: str = ""

    cadence: Cadence = "weekly"
    enabled: bool = False

    raw_table: str = Field(description="Table name written in RAW.OPENFDA")
    row_limit: int | None = Field(
        default=MAX_SKIP,
        description=(
            "Safety cap on rows pulled per run, itself capped at openFDA's "
            f"{MAX_SKIP}-row `skip` ceiling — see MAX_SKIP."
        ),
    )

    endpoint: str = Field(
        description="openFDA endpoint path under api.fda.gov, e.g. 'drug/enforcement'"
    )
    search: str | None = Field(
        default=None,
        description=(
            "openFDA Lucene-syntax `search` query narrowing this endpoint, e.g. "
            "'country:\"United States\"'. https://open.fda.gov/apis/query-syntax/"
        ),
    )
