"""One asset job per HealthData.gov cadence bucket.

The job itself is built by ``ohdp_orchestration.jobs.socrata.cadence_job`` —
see there for how the selection works.
"""

from __future__ import annotations

from ohdp_ingestion.healthdata_gov import HEALTHDATA_GOV
from ohdp_orchestration.jobs.socrata import cadence_job

healthdata_gov_daily_ingest_job = cadence_job(HEALTHDATA_GOV, "daily")
healthdata_gov_weekly_ingest_job = cadence_job(HEALTHDATA_GOV, "weekly")
healthdata_gov_monthly_ingest_job = cadence_job(HEALTHDATA_GOV, "monthly")
