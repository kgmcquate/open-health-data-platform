"""Cron schedules for the CDC cadence jobs (``ohdp_orchestration.jobs.cdc``).

Stopped by default until a deploy turns them on (mirrors the HealthData.gov and
OpenMetadata sync schedules). Fire at 06:00 UTC — an hour ahead of
HealthData.gov's 07:00 and two ahead of the 08:00 dbt build, so with the run
launcher's ``maxConcurrentRuns: 1`` (ARCHITECTURE.md §3/§4) both ingest buckets
drain from the queue before the build starts.
"""

from __future__ import annotations

from dagster import DefaultScheduleStatus, ScheduleDefinition

from ohdp_orchestration.jobs.cdc import (
    cdc_daily_ingest_job,
    cdc_monthly_ingest_job,
    cdc_weekly_ingest_job,
)

cdc_daily_schedule = ScheduleDefinition(
    name="cdc_daily_schedule",
    job=cdc_daily_ingest_job,
    cron_schedule="0 6 * * *",
    default_status=DefaultScheduleStatus.STOPPED,
)

cdc_weekly_schedule = ScheduleDefinition(
    name="cdc_weekly_schedule",
    job=cdc_weekly_ingest_job,
    cron_schedule="0 6 * * 1",
    default_status=DefaultScheduleStatus.STOPPED,
)

cdc_monthly_schedule = ScheduleDefinition(
    name="cdc_monthly_schedule",
    job=cdc_monthly_ingest_job,
    cron_schedule="0 6 1 * *",
    default_status=DefaultScheduleStatus.STOPPED,
)
