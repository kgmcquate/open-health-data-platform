"""HealthData.gov ingestion.

HealthData.gov runs on Socrata (Tyler Data & Insights). Two Socrata surfaces do
all the work here, so nothing scrapes HTML:

* the **catalog API** (``/api/catalog/v1``) enumerates datasets with their
  column schema, update cadence, and popularity — see :mod:`.catalog`;
* the **resource API** (``/resource/{id}.json``) serves the rows as paginated
  SoQL, which :mod:`.source` wraps as a ``dlt`` source.

The catalog is walked once by ``data/scripts/scrape_healthdata_gov.py`` to emit
one YAML per dataset under
``ohdp_orchestration/defs/healthdata_gov/datasets/``. The Dagster component in
that package turns each enabled YAML into a ``dlt``-backed asset at run time.
"""

from ohdp_ingestion.healthdata_gov.catalog import CatalogDataset, iter_catalog
from ohdp_ingestion.healthdata_gov.source import socrata_source

__all__ = ["CatalogDataset", "iter_catalog", "socrata_source"]

DOMAIN = "healthdata.gov"
