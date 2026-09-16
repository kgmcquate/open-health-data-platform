"""What distinguishes one Socrata catalog from another.

Everything else about Socrata ingestion — the catalog walk, the per-dataset
config contract, the ``dlt`` source, the Dagster component — is identical
across domains (ADR-0018). The differences fit in this struct, and each
concrete source package binds one instance of it:
``ohdp_ingestion.healthdata_gov.HEALTHDATA_GOV``,
``ohdp_ingestion.cdc.CDC``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SocrataDomain:
    """One Socrata-hosted open-data catalog.

    ``source`` is the identity that shows up everywhere downstream and must
    never change once data has landed: it is the raw namespace
    (``raw_<source>``, ADR-0019), the middle segment of every asset key, the
    dbt source name, the ``domain`` tag the cadence jobs select on, and the
    ``dlt`` schema name recorded in the destination's pipeline state.
    """

    domain: str
    """Socrata host, e.g. ``"healthdata.gov"`` — both the catalog API's
    ``domains=`` filter and the ``/resource/{id}.json`` host."""

    source: str
    """Stable snake_case identity for the domain, e.g. ``"healthdata_gov"``."""

    title: str
    """Human name, for generated descriptions, e.g. ``"HealthData.gov"``."""

    app_token_env: str
    """Env var holding an optional Socrata app token. A token only raises the
    rate limit; every domain here is readable anonymously."""

    def landing_page(self, dataset_id: str) -> str:
        """Fallback dataset URL when the catalog gives no permalink."""
        return f"https://{self.domain}/d/{dataset_id}"
