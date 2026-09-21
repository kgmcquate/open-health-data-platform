"""One asset job per CMS (data.cms.gov) cadence bucket.

The job itself is built by ``ohdp_orchestration.jobs.cadence.cadence_job`` —
see there for how the selection works.
"""

from __future__ import annotations

from ohdp_orchestration.jobs.cadence import cadence_job

cms_daily_ingest_job = cadence_job("cms", "CMS", "daily")
cms_weekly_ingest_job = cadence_job("cms", "CMS", "weekly")
cms_monthly_ingest_job = cadence_job("cms", "CMS", "monthly")
