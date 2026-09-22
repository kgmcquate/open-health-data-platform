"""The per-dataset config contract for openFDA ingestion.

One YAML file lives at ``ohdp_orchestration/defs/openfda/datasets/defs.yaml``,
hand-maintained (not scraped — see the package docstring for why) and read by
the Dagster component (``ohdp_orchestration.defs.openfda.component.OpenFDADataset``).

Unlike Socrata's ``ColumnSpec``/``incremental_cursor`` (a system column on a
catalog entry), an openFDA "dataset" is just which of openFDA's fixed REST
endpoints (``drug/enforcement``, ``food/enforcement``, ...) this instance
pulls, optionally narrowed by ``search`` — see ``ohdp_ingestion.cms.config`` /
``ohdp_ingestion.openaq.config`` for the same reasoning applied to CMS/OpenAQ.

``incremental_cursor``/``primary_key`` are still opt-in per instance: an
endpoint only gets merge-on-cursor loading (see ``ohdp_ingestion.openfda.source``)
if it has both a documented date field to page against and a documented unique
identifier to upsert on. Leave both unset to fall back to a full replace, the
same way Socrata's ``DatasetConfig`` falls back to ``replace`` when
``incremental_cursor`` is unset.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from ohdp_ingestion.cadence import CADENCES, Cadence

__all__ = ["CADENCES", "Cadence", "DatasetConfig"]


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
        default=None,
        description=(
            "Optional safety cap on total rows pulled per run. Unlike the old "
            "`skip`-based paging, ohdp_ingestion.openfda.source now pages past "
            "openFDA's 25,000-row `skip` ceiling via search_after cursoring, so "
            "this is a cost/runtime valve, not a workaround for an API limit."
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

    incremental_cursor: str | None = Field(
        default=None,
        description=(
            "openFDA date field (yyyymmdd, e.g. 'report_date') to page against "
            "incrementally and sort ascending. When set, `primary_key` must be "
            "set too and loading switches from full `replace` to `merge`-on-cursor "
            "-- see ohdp_ingestion.openfda.source for the rolling-window `lag` "
            "that re-syncs status changes (e.g. Ongoing -> Terminated) on "
            "already-ingested rows near the cursor."
        ),
    )
    primary_key: str | None = Field(
        default=None,
        description=(
            "Unique-record field to upsert on when `incremental_cursor` is set, "
            "e.g. 'recall_number' for the enforcement endpoints."
        ),
    )
    incremental_lag_days: int = Field(
        default=90,
        description=(
            "Rolling window: days to look back from the last high-water mark on "
            "every run, re-fetching (and re-merging) that trailing window so "
            "post-publication field changes on recent rows aren't missed. Only "
            "used when `incremental_cursor` is set."
        ),
    )

    @model_validator(mode="after")
    def _incremental_cursor_and_primary_key_come_together(self) -> DatasetConfig:
        if bool(self.incremental_cursor) != bool(self.primary_key):
            raise ValueError(
                "incremental_cursor and primary_key must be set together: merge "
                "loading needs both a cursor to page against and a key to upsert on"
            )
        return self
