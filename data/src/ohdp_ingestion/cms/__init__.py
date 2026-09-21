"""CMS (data.cms.gov) ingestion.

CMS is *not* Socrata or CKAN — ADR-0018's closing note guessed it might be
either, and neither panned out (confirmed against Socrata's own domain
discovery API, which 404s on every plausible CMS host). CMS publishes its own
``data-api/v1`` REST API, discoverable through the federal Project Open Data
catalog at ``data.cms.gov/data.json`` (ADR-0026).

Two CMS surfaces do the work:

* the **catalog** (``data.cms.gov/data.json``) enumerates every dataset series
  with its current API distribution, publisher and update cadence — see
  :mod:`.catalog`;
* the **data API** (``data.cms.gov/data-api/v1/dataset/{uuid}/data``) serves
  the rows, paginated by offset/size — :mod:`.source` wraps it as a ``dlt``
  source (dlt's built-in ``rest_api_source``, unlike Socrata's hand-rolled
  SoQL client, because CMS's API needs nothing bespoke).

Both land in ``RAW.CMS`` through the same shared Iceberg-over-Horizon
destination Socrata sources use
(:mod:`ohdp_ingestion.iceberg_destination`) — that machinery was never
Socrata-specific to begin with, just never pulled out until CMS needed it too.

``data/scripts/scrape_cms.py`` walks the catalog once and emits one YAML per
dataset under ``ohdp_orchestration/defs/cms/datasets/``; the Dagster component
in that package (``ohdp_orchestration.defs.cms.component.CMSDataset``) turns
each enabled YAML into a ``dlt``-backed asset at run time — the same shape as
Socrata's ``SocrataDataset``, just not derived from it (there is only one CMS
domain, so the base/subclass split ADR-0018 needed for multiple Socrata
domains has nothing to buy here).
"""

from ohdp_ingestion.cms.catalog import CatalogDataset, iter_catalog
from ohdp_ingestion.cms.config import CADENCES, Cadence, DatasetConfig
from ohdp_ingestion.cms.source import CMS_API_BASE_URL, cms_source
from ohdp_ingestion.iceberg_destination import build_pipeline

__all__ = [
    "CADENCES",
    "CMS_API_BASE_URL",
    "Cadence",
    "CatalogDataset",
    "DatasetConfig",
    "build_pipeline",
    "cms_source",
    "iter_catalog",
]
