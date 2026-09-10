"""The per-dataset config contract.

One YAML file per dataset lives in
``ohdp_orchestration/defs/healthdata_gov/datasets/``. The scraper writes them;
the Dagster component reads them. This module is the single source of truth for
their shape so the two halves cannot drift.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Literal, cast

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from ohdp_ingestion.healthdata_gov.catalog import CatalogDataset

Cadence = Literal["daily", "weekly", "monthly"]

# dlt/DuckDB schema that every healthdata.gov table lands in. Kept separate from
# the M0 `main` schema so a generated table can never shadow a hand-written one.
DATASET_SCHEMA = "healthdata_gov"


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return re.sub(r"_+", "_", slug) or "dataset"


class ColumnSpec(BaseModel):
    """One Socrata column, as advertised by the catalog (not the loaded table)."""

    model_config = {"extra": "forbid"}

    name: str
    type: str = Field(description="Socrata datatype, lowercased (text, number, calendar_date, ...)")
    description: str = ""


class DatasetConfig(BaseModel):
    """A single HealthData.gov dataset to ingest."""

    model_config = {"extra": "forbid"}

    id: str = Field(description="Socrata 4x4 identifier, e.g. 879u-23sm")
    name: str
    publisher: str = ""
    description: str = ""
    source_url: str = ""

    cadence: Cadence = "weekly"
    enabled: bool = False

    raw_table: str = Field(description="Table name written in the healthdata_gov schema")
    incremental_cursor: str | None = Field(
        default="socrata_updated_at",
        description="Cursor column for merge loads; null forces a full replace",
    )
    row_limit: int | None = Field(default=None, description="Safety cap on rows pulled per run")

    page_views: int = 0
    keywords: list[str] = Field(default_factory=list)
    columns: list[ColumnSpec] = Field(
        default_factory=list,
        description="Column schema from the Socrata catalog, surfaced as Dagster TableSchema",
    )

    @property
    def slug(self) -> str:
        return slugify(self.name)

    @classmethod
    def from_catalog(
        cls,
        dataset: CatalogDataset,
        *,
        enabled: bool = False,
        row_limit: int | None = None,
    ) -> DatasetConfig:
        slug = slugify(dataset.name)
        return cls(
            id=dataset.id,
            name=dataset.name,
            publisher=dataset.publisher,
            description=dataset.description[:600],
            source_url=dataset.landing_page,
            cadence=cast(Cadence, dataset.cadence),
            enabled=enabled,
            raw_table=f"raw_healthdata_gov__{slug}",
            incremental_cursor="socrata_updated_at",
            row_limit=row_limit,
            page_views=dataset.page_views_total,
            keywords=dataset.keywords,
            columns=[
                ColumnSpec(name=c.name, type=c.datatype, description=c.description)
                for c in dataset.columns
                if c.name
            ],
        )
