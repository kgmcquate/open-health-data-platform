"""OpenAQ — global air quality data. https://docs.openaq.org

Two resources, neither a bulk multi-dataset catalog the way Socrata/CMS are
(ADR-0008, ADR-0026), so there's no scraper or catalog module here — just
``dlt`` sources (:mod:`.source`) feeding the same shared Iceberg-over-Horizon
destination every other raw source uses
(:mod:`ohdp_ingestion.iceberg_destination`):

* ``locations`` — every monitoring station's metadata, one paginated pull.
* ``monthly_measurements`` — monthly-average readings, fanned out per sensor
  (see ``openaq_monthly_measurements_source``'s docstring for why that fan-out
  is unavoidable and what keeps its API-call cost bounded).

Requires an API key (header ``X-API-Key``, free tier) — already wired end to
end as ``OHDP_OPENAQ_API_KEY`` (``platform/helm/README.md``,
``.github/workflows/deploy-platform.yml``).

``data/src/ohdp_orchestration/defs/openaq/component.py`` turns each enabled
instance in ``datasets/defs.yaml`` into a ``dlt``-backed asset at run time —
the same three-asset shape as Socrata/CMS sources
(``ohdp_orchestration.defs.cms.component.CMSDataset``).
"""

from ohdp_ingestion.iceberg_destination import build_pipeline
from ohdp_ingestion.openaq.config import CADENCES, Cadence, DatasetConfig
from ohdp_ingestion.openaq.source import (
    OPENAQ_API_BASE_URL,
    openaq_locations_source,
    openaq_monthly_measurements_source,
)

__all__ = [
    "CADENCES",
    "Cadence",
    "DatasetConfig",
    "OPENAQ_API_BASE_URL",
    "build_pipeline",
    "openaq_locations_source",
    "openaq_monthly_measurements_source",
]
