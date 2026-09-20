"""Schedules for the OpenMetadata sync jobs (``ohdp_orchestration.jobs.openmetadata_sync``).

Both default STOPPED until OHDP_OPENMETADATA_JWT is confirmed live in the
target environment (mirrors the healthdata_gov cadence schedules' default)."""

from __future__ import annotations

from dagster import DefaultScheduleStatus, ScheduleDefinition

from ohdp_orchestration.jobs.openmetadata_sync import (
    openmetadata_dagster_sync_job,
    openmetadata_dbt_sync_job,
    openmetadata_seed_sync_job,
    openmetadata_snowflake_sync_job,
)

openmetadata_dagster_sync_schedule = ScheduleDefinition(
    name="openmetadata_dagster_sync_schedule",
    job=openmetadata_dagster_sync_job,
    cron_schedule="0 * * * *",
    default_status=DefaultScheduleStatus.STOPPED,
)

# Domains/glossary terms have no dependency on the other syncs (they're
# standalone reference entities, not attached to any table/endpoint yet), so
# this just runs early in the same catalog-refresh window.
openmetadata_seed_sync_schedule = ScheduleDefinition(
    name="openmetadata_seed_sync_schedule",
    job=openmetadata_seed_sync_job,
    cron_schedule="50 5 * * *",
    default_status=DefaultScheduleStatus.STOPPED,
)

openmetadata_snowflake_sync_schedule = ScheduleDefinition(
    name="openmetadata_snowflake_sync_schedule",
    job=openmetadata_snowflake_sync_job,
    cron_schedule="0 6 * * *",
    default_status=DefaultScheduleStatus.STOPPED,
)

# 30 minutes after openmetadata_snowflake_sync_schedule: dbt ingestion attaches
# metadata to tables that sync must have already created in OpenMetadata.
openmetadata_dbt_sync_schedule = ScheduleDefinition(
    name="openmetadata_dbt_sync_schedule",
    job=openmetadata_dbt_sync_job,
    cron_schedule="30 6 * * *",
    default_status=DefaultScheduleStatus.STOPPED,
)
