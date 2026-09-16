"""HealthData.gov ingestion — the HealthData.gov binding of the shared Socrata
machinery in :mod:`ohdp_ingestion.socrata` (ADR-0008, ADR-0018).

Everything that does the work — the catalog walk, the ``DatasetConfig``
contract, the ``dlt`` source, the Dagster component — is domain-agnostic and
lives there. All that is HealthData.gov-specific is the constant below.
"""

from ohdp_ingestion.socrata import SocrataDomain

HEALTHDATA_GOV = SocrataDomain(
    domain="healthdata.gov",
    source="healthdata_gov",
    title="HealthData.gov",
    app_token_env="OHDP_HEALTHDATA_APP_TOKEN",
)

__all__ = ["HEALTHDATA_GOV"]
