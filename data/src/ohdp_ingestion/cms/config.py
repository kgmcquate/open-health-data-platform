"""The per-dataset config contract for CMS ingestion.

One YAML file lives at ``ohdp_orchestration/defs/cms/datasets/defs.yaml``,
written by ``scripts/scrape_cms.py`` and read by the Dagster component
(``ohdp_orchestration.defs.cms.component.CMSDataset``) — this module is the
single source of truth for its shape, the same role
``ohdp_ingestion.socrata.config.DatasetConfig`` plays for Socrata sources
(ADR-0018, ADR-0026).

No ``incremental_cursor`` here, unlike Socrata: CMS's ``data-api/v1`` has no
per-row ``:id``/``:updated_at`` system columns — every distribution is a
whole-dataset republish on its own cadence, not an appended change log — so
every CMS table is a full replace each run.

``ColumnSpec`` means the same thing it does on the Socrata side, but is not
shared with it: a Socrata column's ``type`` is a Socrata datatype off the
catalog, a CMS column's is the backing CSV's advertised type, and neither
vocabulary is the other's. CMS also does not put columns in ``data.json`` at
all — see :mod:`ohdp_ingestion.cms.catalog` for the two extra surfaces they
come from.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from pydantic import BaseModel, Field

from ohdp_ingestion.cadence import CADENCES, Cadence
from ohdp_ingestion.naming import table_name

if TYPE_CHECKING:
    from ohdp_ingestion.cms.catalog import CatalogDataset

__all__ = ["CADENCES", "Cadence", "ColumnSpec", "DatasetConfig"]


class ColumnSpec(BaseModel):
    """One CMS column, as the latest distribution advertises it."""

    model_config = {"extra": "forbid"}

    name: str
    type: str = Field(default="", description="CSV datatype, lowercased (text, numeric, date)")
    description: str = Field(
        default="", description="From the dataset's data dictionary page, when it has one"
    )


class DatasetConfig(BaseModel):
    """A single CMS dataset series to ingest — its latest API distribution."""

    model_config = {"extra": "forbid"}

    id: str = Field(description="UUID of the dataset's latest API distribution")
    name: str
    publisher: str = ""
    description: str = ""
    source_url: str = ""

    cadence: Cadence = "weekly"
    enabled: bool = False

    raw_table: str = Field(description="Table name written in RAW.CMS")
    row_limit: int | None = Field(default=None, description="Safety cap on rows pulled per run")

    row_count: int = Field(default=0, description="Informational total-row count at scrape time")
    keywords: list[str] = Field(default_factory=list)
    columns: list[ColumnSpec] = Field(
        default_factory=list,
        description="Column schema from the latest distribution, surfaced as Dagster TableSchema",
    )

    @classmethod
    def from_catalog(
        cls,
        dataset: CatalogDataset,
        *,
        enabled: bool = False,
        row_limit: int | None = None,
    ) -> DatasetConfig:
        return cls(
            id=dataset.id,
            name=dataset.name,
            publisher=dataset.publisher,
            description=dataset.description[:600],
            source_url=dataset.landing_page,
            cadence=cast(Cadence, dataset.cadence),
            enabled=enabled,
            raw_table=table_name(dataset.name, dataset.id),
            row_limit=row_limit,
            row_count=dataset.row_count,
            keywords=dataset.keywords,
            columns=[
                ColumnSpec(name=c.name, type=c.type, description=c.description)
                for c in dataset.columns
            ],
        )
