"""Cron schedules for the HealthData.gov cadence jobs
(``ohdp_orchestration.jobs.healthdata_gov``).

Stopped by default until a deploy turns them on (mirrors the OpenMetadata
sync schedules). Fire at 07:00 UTC, before the 08:00 dbt build.
"""

from __future__ import annotations

from dagster import DefaultScheduleStatus, ScheduleDefinition

from ohdp_orchestration.jobs.healthdata_gov import (
    healthdata_gov_daily_ingest_job,
    healthdata_gov_monthly_ingest_job,
    healthdata_gov_weekly_ingest_job,
)

healthdata_gov_daily_schedule = ScheduleDefinition(
    name="healthdata_gov_daily_schedule",
    job=healthdata_gov_daily_ingest_job,
    cron_schedule="0 7 * * *",
    default_status=DefaultScheduleStatus.STOPPED,
)

healthdata_gov_weekly_schedule = ScheduleDefinition(
    name="healthdata_gov_weekly_schedule",
    job=healthdata_gov_weekly_ingest_job,
    cron_schedule="0 7 * * 1",
    default_status=DefaultScheduleStatus.STOPPED,
)

healthdata_gov_monthly_schedule = ScheduleDefinition(
    name="healthdata_gov_monthly_schedule",
    job=healthdata_gov_monthly_ingest_job,
    cron_schedule="0 7 1 * *",
    default_status=DefaultScheduleStatus.STOPPED,
)
