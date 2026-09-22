"""One asset job per OpenAQ cadence bucket.

The job itself is built by ``ohdp_orchestration.jobs.cadence.cadence_job`` —
see there for how the selection works.
"""

from __future__ import annotations

from ohdp_orchestration.jobs.cadence import cadence_job

openaq_daily_ingest_job = cadence_job("openaq", "OpenAQ", "daily")
openaq_weekly_ingest_job = cadence_job("openaq", "OpenAQ", "weekly")
openaq_monthly_ingest_job = cadence_job("openaq", "OpenAQ", "monthly")
