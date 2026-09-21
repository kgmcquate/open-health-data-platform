"""Cron schedules for the CMS cadence jobs (``ohdp_orchestration.jobs.cms``).

Stopped by default until a deploy turns them on (mirrors the CDC and
HealthData.gov ingestion schedules). Fire at 05:00 UTC — an hour ahead of
CDC's 06:00, two ahead of HealthData.gov's 07:00, three ahead of the 08:00
dbt build, so with the run launcher's ``maxConcurrentRuns: 1``
(ARCHITECTURE.md §3/§4) every ingest bucket drains from the queue before the
build starts.
"""

from __future__ import annotations

from dagster import DefaultScheduleStatus, ScheduleDefinition

from ohdp_orchestration.jobs.cms import (
    cms_daily_ingest_job,
    cms_monthly_ingest_job,
    cms_weekly_ingest_job,
)

cms_daily_schedule = ScheduleDefinition(
    name="cms_daily_schedule",
    job=cms_daily_ingest_job,
    cron_schedule="0 5 * * *",
    default_status=DefaultScheduleStatus.STOPPED,
)

cms_weekly_schedule = ScheduleDefinition(
    name="cms_weekly_schedule",
    job=cms_weekly_ingest_job,
    cron_schedule="0 5 * * 1",
    default_status=DefaultScheduleStatus.STOPPED,
)

cms_monthly_schedule = ScheduleDefinition(
    name="cms_monthly_schedule",
    job=cms_monthly_ingest_job,
    cron_schedule="0 5 1 * *",
    default_status=DefaultScheduleStatus.STOPPED,
)
