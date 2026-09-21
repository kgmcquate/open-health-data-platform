"""The per-dataset config contract, shared by every Socrata domain.

One YAML file per dataset lives in
``ohdp_orchestration/defs/<source>/datasets/``. ``scripts/scrape_socrata.py``
writes them; the Dagster component reads them. This module is the single source
of truth for their shape so the two halves cannot drift (ADR-0008, ADR-0018).

Nothing here is domain-specific: what varies between HealthData.gov and
data.cdc.gov lives in :class:`~ohdp_ingestion.socrata.domain.SocrataDomain`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from pydantic import BaseModel, Field, field_validator

from ohdp_ingestion.cadence import CADENCES, Cadence
from ohdp_ingestion.naming import slugify, table_name

if TYPE_CHECKING:
    from ohdp_ingestion.socrata.catalog import CatalogDataset

__all__ = [
    "CADENCES",
    "Cadence",
    "ColumnSpec",
    "DatasetConfig",
    "slugify",
    "table_name",
]


def _as_text(value: Any) -> str:
    """Take a catalog value back as text after Dagster has re-typed it.

    Dagster resolves every string in a component's ``defs.yaml`` through Jinja's
    *native* environment, which ``ast.literal_eval``s whatever it renders. A
    catalog value that happens to read as a Python literal therefore arrives
    here already coerced — Socrata's year tags (``"2019"``) as ``int``, and the
    junk tag ``"..."`` as ``Ellipsis``. Both are quoted correctly in the YAML
    and survive a plain ``yaml.safe_load``; the coercion happens after parsing,
    so quoting cannot prevent it.

    Catalog metadata is free text and none of it is worth failing a dataset
    over, so convert it back. ``Ellipsis`` is the one value with no sensible
    text form — it was punctuation-only noise to begin with — so it empties out
    and the keyword validator below drops it.

    Wired up with ``field_validator``, not an ``Annotated`` alias: Dagster reads
    a component's YAML schema off these annotations and treats *any* ``Annotated``
    metadata as one of its own ``Resolver``s, so a ``BeforeValidator`` there is
    rejected outright ("includes incompatible field").
    """
    if isinstance(value, str):
        return value
    if value is None or value is Ellipsis:
        return ""
    return str(value)


class ColumnSpec(BaseModel):
    """One Socrata column, as advertised by the catalog (not the loaded table)."""

    model_config = {"extra": "forbid"}

    name: str
    type: str = Field(description="Socrata datatype, lowercased (text, number, calendar_date, ...)")
    description: str = ""

    _as_text = field_validator("description", mode="before")(_as_text)


class DatasetConfig(BaseModel):
    """A single Socrata dataset to ingest."""

    model_config = {"extra": "forbid"}

    id: str = Field(description="Socrata 4x4 identifier, e.g. 879u-23sm")
    name: str
    publisher: str = ""
    description: str = ""
    source_url: str = ""

    cadence: Cadence = "weekly"
    enabled: bool = False

    raw_table: str = Field(description="Table name written in the source's raw_<source> namespace")
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

    _text = field_validator("name", "publisher", "description", mode="before")(_as_text)

    @field_validator("keywords", mode="before")
    @classmethod
    def _keywords_as_text(cls, value: Any) -> Any:
        """Same coercion per tag, then drop what it emptied out (the ``"..."``
        tag) along with any blank tag the catalog itself carried."""
        if not isinstance(value, list):
            return value
        return [text for text in (_as_text(tag) for tag in value) if text.strip()]

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
        return cls(
            id=dataset.id,
            name=dataset.name,
            publisher=dataset.publisher,
            description=dataset.description[:600],
            source_url=dataset.landing_page,
            cadence=cast(Cadence, dataset.cadence),
            enabled=enabled,
            raw_table=table_name(dataset.name, dataset.id),
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
