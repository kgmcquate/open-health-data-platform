"""Schedule for the per-dataset literature relevance sync job
(``ohdp_orchestration.jobs.literature_dataset_sync``).

Monthly, same cadence rationale as the asset it replaced: relevance search
results over an OpenAlex index that itself updates gradually don't move fast
enough to justify more frequent runs, and this is one OpenAlex call per
ingested dataset (~40 today) rather than one per subfield, so cost isn't the
constraint either. Stopped by default until OHDP_OPENMETADATA_JWT and
OHDP_OPENALEX_CONTACT_EMAIL are confirmed live in the target environment
(mirrors every other OpenMetadata sync schedule)."""

from __future__ import annotations

from dagster import DefaultScheduleStatus, ScheduleDefinition

from ohdp_orchestration.jobs.literature_dataset_sync import literature_dataset_sync_job

# 5 minutes after openmetadata_dbt_sync (06:30 UTC daily) — this asset's
# lineage step needs the raw-layer Table entity to already exist in OM
# (written by openmetadata_snowflake_sync, 06:00) and reads the live Dagster
# asset graph the same way openmetadata_dagster_sync (hourly) does, so both
# of those have always run well before this fires once a month.
literature_dataset_sync_schedule = ScheduleDefinition(
    name="literature_dataset_sync_schedule",
    job=literature_dataset_sync_job,
    cron_schedule="35 6 1 * *",
    default_status=DefaultScheduleStatus.STOPPED,
)
