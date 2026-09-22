"""Cron schedules for the openFDA cadence jobs (``ohdp_orchestration.jobs.openfda``).

Stopped by default until a deploy turns them on (mirrors CDC/CMS/HealthData.gov/
OpenAQ). Fire at 04:30 UTC — 30 minutes after OpenAQ's 04:00 and 30 minutes
ahead of CMS's 05:00, the earliest of the five raw ingestion buckets after
OpenAQ, so with the run launcher's ``maxConcurrentRuns: 1``
(ARCHITECTURE.md §3/§4) it drains from the queue well before CMS/CDC/HealthData.gov
start and none of them queue behind it (three enforcement-report endpoints,
each capped at 25,000 rows -- see ohdp_orchestration.defs.openfda's README --
not ~150 datasets like HealthData.gov).
"""

from __future__ import annotations

from dagster import DefaultScheduleStatus, ScheduleDefinition

from ohdp_orchestration.jobs.openfda import (
    openfda_daily_ingest_job,
    openfda_monthly_ingest_job,
    openfda_weekly_ingest_job,
)

openfda_daily_schedule = ScheduleDefinition(
    name="openfda_daily_schedule",
    job=openfda_daily_ingest_job,
    cron_schedule="30 4 * * *",
    default_status=DefaultScheduleStatus.STOPPED,
)

openfda_weekly_schedule = ScheduleDefinition(
    name="openfda_weekly_schedule",
    job=openfda_weekly_ingest_job,
    cron_schedule="30 4 * * 1",
    default_status=DefaultScheduleStatus.STOPPED,
)

openfda_monthly_schedule = ScheduleDefinition(
    name="openfda_monthly_schedule",
    job=openfda_monthly_ingest_job,
    cron_schedule="30 4 1 * *",
    default_status=DefaultScheduleStatus.STOPPED,
)
