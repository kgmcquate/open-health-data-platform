"""CDC open data ingestion — the data.cdc.gov binding of the shared Socrata
machinery in :mod:`ohdp_ingestion.socrata` (ADR-0018).

data.cdc.gov is a Socrata deployment like HealthData.gov, so it needs no client
of its own: the catalog walk, the ``DatasetConfig`` contract, the ``dlt``
source and the Dagster component are all shared. All that is CDC-specific is
the constant below.

The catalog is roughly 1,000 datasets — NNDSS weekly notifiable disease tables,
NCHS mortality and natality, BRFSS/PLACES, wastewater surveillance,
vaccination coverage, and so on. Raw tables land in ``RAW.CDC``.
"""

from ohdp_ingestion.socrata import SocrataDomain

CDC = SocrataDomain(
    domain="data.cdc.gov",
    source="cdc",
    title="CDC Open Data",
    app_token_env="OHDP_CDC_APP_TOKEN",
)

__all__ = ["CDC"]
