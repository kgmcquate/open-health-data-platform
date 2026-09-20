"""Schedule for the curated literature sync job
(``ohdp_orchestration.jobs.literature``).

Monthly, not daily/weekly like the HealthData.gov cadence buckets — citation
rankings don't move fast enough to justify more frequent runs. Stopped by
default until OHDP_OPENMETADATA_JWT and OHDP_OPENALEX_CONTACT_EMAIL are
confirmed live in the target environment (mirrors every other OpenMetadata
sync schedule)."""

from __future__ import annotations

from dagster import DefaultScheduleStatus, ScheduleDefinition

from ohdp_orchestration.jobs.literature import literature_sync_job

# 45 minutes after the seed sync (05:50 UTC) — domains must already exist
# before a page can be tagged to one.
literature_sync_schedule = ScheduleDefinition(
    name="literature_sync_schedule",
    job=literature_sync_job,
    cron_schedule="35 6 1 * *",
    default_status=DefaultScheduleStatus.STOPPED,
)
