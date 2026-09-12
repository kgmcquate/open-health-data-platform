"""One asset job per HealthData.gov cadence bucket.

Each selects table assets by their ``cadence`` tag (set in
``ohdp_orchestration.defs.healthdata_gov.component``) rather than by name, so
it needs no knowledge of the dataset instances — adding a dataset never
touches this module. A cadence with nothing enabled yet is an empty job (its
scheduled run is a no-op).
"""

from __future__ import annotations

from dagster import AssetSelection, define_asset_job

from ohdp_ingestion.healthdata_gov.config import Cadence

_DOMAIN = "healthdata_gov"


def _cadence_job(cadence: Cadence):
    selection = AssetSelection.tag("domain", _DOMAIN) & AssetSelection.tag("cadence", cadence)
    return define_asset_job(
        name=f"healthdata_gov_{cadence}_ingest",
        selection=selection,
        description=f"HealthData.gov {cadence} ingestion bucket.",
        tags={
            "domain": _DOMAIN,
            "cadence": cadence
        },
        run_tags={
            "dagster/max_concurrent": "2",
            "dagster-k8s/config": {
                "container_config": {
                    "resources": {
                        "requests": {"memory": "1Gi"},
                        "limits": {"memory": "2Gi"},
                    }
                }
            },
        }
    )


healthdata_gov_daily_ingest_job = _cadence_job("daily")
healthdata_gov_weekly_ingest_job = _cadence_job("weekly")
healthdata_gov_monthly_ingest_job = _cadence_job("monthly")
