"""Cron schedules for the OpenAQ cadence jobs (``ohdp_orchestration.jobs.openaq``).

Stopped by default until a deploy turns them on (mirrors CDC/CMS/HealthData.gov).
Fire at 04:00 UTC — an hour ahead of CMS's 05:00, the earliest of the four raw
ingestion buckets, so with the run launcher's ``maxConcurrentRuns: 1``
(ARCHITECTURE.md §3/§4) it drains from the queue well before CMS/CDC/HealthData.gov
start and none of them queue behind it (OpenAQ's `locations` snapshot is one
small, fast-running table, not ~150 datasets like HealthData.gov).
"""

from __future__ import annotations

from dagster import DefaultScheduleStatus, ScheduleDefinition

from ohdp_orchestration.jobs.openaq import (
    openaq_daily_ingest_job,
    openaq_monthly_ingest_job,
    openaq_weekly_ingest_job,
)

openaq_daily_schedule = ScheduleDefinition(
    name="openaq_daily_schedule",
    job=openaq_daily_ingest_job,
    cron_schedule="0 4 * * *",
    default_status=DefaultScheduleStatus.STOPPED,
)

openaq_weekly_schedule = ScheduleDefinition(
    name="openaq_weekly_schedule",
    job=openaq_weekly_ingest_job,
    cron_schedule="0 4 * * 1",
    default_status=DefaultScheduleStatus.STOPPED,
)

openaq_monthly_schedule = ScheduleDefinition(
    name="openaq_monthly_schedule",
    job=openaq_monthly_ingest_job,
    cron_schedule="0 4 1 * *",
    default_status=DefaultScheduleStatus.STOPPED,
)
