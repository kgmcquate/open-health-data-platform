"""One asset job per CDC (data.cdc.gov) cadence bucket.

The job itself is built by ``ohdp_orchestration.jobs.cadence.cadence_job`` —
see there for how the selection works.
"""

from __future__ import annotations

from ohdp_ingestion.cdc import CDC
from ohdp_orchestration.jobs.cadence import cadence_job

cdc_daily_ingest_job = cadence_job(CDC.source, CDC.title, "daily")
cdc_weekly_ingest_job = cadence_job(CDC.source, CDC.title, "weekly")
cdc_monthly_ingest_job = cadence_job(CDC.source, CDC.title, "monthly")
