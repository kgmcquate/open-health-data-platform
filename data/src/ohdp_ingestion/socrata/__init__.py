"""Socrata (SODA) ingestion, shared by every Socrata-hosted catalog.

HealthData.gov and data.cdc.gov are both Tyler Data & Insights (Socrata)
deployments, so one set of primitives serves both — see ADR-0018. Two Socrata
surfaces do all the work, so nothing scrapes HTML:

* the **catalog API** (``api.us.socrata.com/api/catalog/v1``) enumerates a
  domain's datasets with their column schema, update cadence and popularity —
  see :mod:`.catalog`;
* the **resource API** (``https://<domain>/resource/{id}.json``) serves the rows
  as paginated SoQL, which :mod:`.source` wraps as a ``dlt`` source.

What differs between domains is only :class:`.SocrataDomain`; each concrete
source package (``ohdp_ingestion.healthdata_gov``, ``ohdp_ingestion.cdc``)
binds one. ``data/scripts/scrape_socrata.py`` walks a domain's catalog once and
emits one YAML per dataset under
``ohdp_orchestration/defs/<source>/datasets/``; the Dagster component in that
package (a subclass of ``ohdp_orchestration.components.socrata.SocrataDataset``)
turns each enabled YAML into a ``dlt``-backed asset at run time.
"""

from ohdp_ingestion.socrata.catalog import CatalogDataset, iter_catalog
from ohdp_ingestion.socrata.config import (
    CADENCES,
    Cadence,
    ColumnSpec,
    DatasetConfig,
    slugify,
    table_name,
)
from ohdp_ingestion.socrata.domain import SocrataDomain
from ohdp_ingestion.socrata.source import (
    build_pipeline,
    configure_iceberg_catalog,
    iceberg_catalog_config,
    socrata_source,
    write_disposition,
)

__all__ = [
    "CADENCES",
    "Cadence",
    "CatalogDataset",
    "ColumnSpec",
    "DatasetConfig",
    "SocrataDomain",
    "build_pipeline",
    "configure_iceberg_catalog",
    "iceberg_catalog_config",
    "iter_catalog",
    "slugify",
    "table_name",
    "socrata_source",
    "write_disposition",
]
