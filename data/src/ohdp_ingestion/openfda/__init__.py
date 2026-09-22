"""openFDA (api.fda.gov) — FDA regulatory data: drug/device/food recall
enforcement reports, adverse events, product labeling and more.
https://open.fda.gov

Like OpenAQ (:mod:`ohdp_ingestion.openaq`), openFDA isn't a bulk multi-dataset
catalog the way Socrata/CMS are (ADR-0008, ADR-0026) — it's a fixed, small set
of well-known REST endpoints under one base URL, each sharing the identical
paginated-JSON-with-envelope response shape. So there's no scraper or catalog
module here, just a single generic ``dlt`` source (:mod:`.source`) parameterized
by ``endpoint``, feeding the same shared Iceberg-over-Horizon destination every
other raw source uses (:mod:`ohdp_ingestion.iceberg_destination`).

An API key is optional here (unlike OpenAQ's, which 401s without one) —
openFDA just rate-limits unauthenticated callers harder (40 req/min, 1,000/day
vs. 240 req/min, 120,000/day with a key). When set, ``OHDP_OPENFDA_API_KEY``
is sent the way openFDA expects it: an ``api_key`` query parameter, not a
header.

``data/src/ohdp_orchestration/defs/openfda/component.py`` turns each enabled
instance in ``datasets/defs.yaml`` into a ``dlt``-backed asset at run time —
the same three-asset shape as Socrata/CMS/OpenAQ sources
(``ohdp_orchestration.defs.cms.component.CMSDataset``).
"""

from ohdp_ingestion.iceberg_destination import build_pipeline
from ohdp_ingestion.openfda.config import CADENCES, Cadence, DatasetConfig
from ohdp_ingestion.openfda.source import OPENFDA_API_BASE_URL, openfda_source

__all__ = [
    "CADENCES",
    "OPENFDA_API_BASE_URL",
    "Cadence",
    "DatasetConfig",
    "build_pipeline",
    "openfda_source",
]
