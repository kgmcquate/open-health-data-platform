"""Schedule for the Cube -> OpenMetadata Metric sync job
(``ohdp_orchestration.jobs.cube_metrics_sync``).

STOPPED by default until OHDP_CUBE_API_SECRET / OHDP_OPENMETADATA_JWT are
confirmed live in the target environment (mirrors the other
openmetadata_sync schedules' default)."""

from __future__ import annotations

from dagster import DefaultScheduleStatus, ScheduleDefinition

from ohdp_orchestration.jobs.cube_metrics_sync import openmetadata_cube_metrics_sync_job

# 45 minutes after openmetadata_snowflake_sync_schedule / 15 after
# openmetadata_dbt_sync_schedule — no ordering dependency on either (this
# sync draws no lineage into Snowflake tables, see the asset module's
# docstring), just grouped into the same early-morning catalog-refresh window.
openmetadata_cube_metrics_sync_schedule = ScheduleDefinition(
    name="openmetadata_cube_metrics_sync_schedule",
    job=openmetadata_cube_metrics_sync_job,
    cron_schedule="45 6 * * *",
    default_status=DefaultScheduleStatus.STOPPED,
)
