"""One asset job per openFDA cadence bucket.

The job itself is built by ``ohdp_orchestration.jobs.cadence.cadence_job`` —
see there for how the selection works.
"""

from __future__ import annotations

from ohdp_orchestration.jobs.cadence import cadence_job

openfda_daily_ingest_job = cadence_job("openfda", "openFDA", "daily")
openfda_weekly_ingest_job = cadence_job("openfda", "openFDA", "weekly")
openfda_monthly_ingest_job = cadence_job("openfda", "openFDA", "monthly")
