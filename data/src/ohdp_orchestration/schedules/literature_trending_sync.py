"""Schedule for the trending-literature sync job
(``ohdp_orchestration.jobs.literature_trending_sync``).

Weekly, not monthly like ``literature_dataset_sync`` — a "trending" feed that
only moves once a month reads as stale, and this is one OpenAlex call per
Domain (~14 today), cheap enough to run more often. Stopped by default until
OHDP_OPENALEX_CONTACT_EMAIL and OHDP_INTERNAL_INGEST_TOKEN are confirmed live
in the target environment (mirrors every other OpenMetadata-adjacent sync
schedule)."""

from __future__ import annotations

from dagster import DefaultScheduleStatus, ScheduleDefinition

from ohdp_orchestration.jobs.literature_trending_sync import literature_trending_sync_job

literature_trending_sync_schedule = ScheduleDefinition(
    name="literature_trending_sync_schedule",
    job=literature_trending_sync_job,
    cron_schedule="0 7 * * 1",  # Monday 07:00 UTC
    default_status=DefaultScheduleStatus.STOPPED,
)
