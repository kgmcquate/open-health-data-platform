"""The single Definitions object for the code location (ARCHITECTURE.md §7).

Everything the daemon and webserver see is assembled here. Keep this file an
assembly point only — no asset bodies, no business logic.

Defs come from two places, merged together:

* ``ohdp_orchestration.defs`` is autoloaded: every Dagster component
  discovered under it (the config-driven HealthData.gov, CDC and CMS
  ingestion) is merged in automatically.
* ``ohdp_orchestration.assets``/``.jobs``/``.schedules``/``.sensors`` are
  plain Dagster objects (no per-instance config, so no Component/defs.yaml
  layer), listed here explicitly — this module is the one place that has to
  know about them.
"""

from __future__ import annotations

from pathlib import Path

from dagster import Definitions
from dagster.components import load_defs

from ohdp_orchestration import defs as _defs_module
from ohdp_orchestration.assets.cube_metrics_sync import openmetadata_cube_metrics_sync
from ohdp_orchestration.assets.lakehouse_dbt import DBT_RESOURCE, lakehouse_dbt_assets
from ohdp_orchestration.assets.literature_dataset_sync import literature_dataset_sync
from ohdp_orchestration.assets.literature_trending_sync import literature_trending_sync
from ohdp_orchestration.assets.openmetadata_dagster_sync import openmetadata_dagster_sync
from ohdp_orchestration.assets.openmetadata_seed_sync import openmetadata_seed_sync
from ohdp_orchestration.assets.openmetadata_sync import (
    openmetadata_dbt_sync,
    openmetadata_snowflake_sync,
)
from ohdp_orchestration.jobs.cdc import (
    cdc_daily_ingest_job,
    cdc_monthly_ingest_job,
    cdc_weekly_ingest_job,
)
from ohdp_orchestration.jobs.cms import (
    cms_daily_ingest_job,
    cms_monthly_ingest_job,
    cms_weekly_ingest_job,
)
from ohdp_orchestration.jobs.cube_metrics_sync import openmetadata_cube_metrics_sync_job
from ohdp_orchestration.jobs.healthdata_gov import (
    healthdata_gov_daily_ingest_job,
    healthdata_gov_monthly_ingest_job,
    healthdata_gov_weekly_ingest_job,
)
from ohdp_orchestration.jobs.literature_dataset_sync import literature_dataset_sync_job
from ohdp_orchestration.jobs.literature_trending_sync import literature_trending_sync_job
from ohdp_orchestration.jobs.openaq import (
    openaq_daily_ingest_job,
    openaq_monthly_ingest_job,
    openaq_weekly_ingest_job,
)
from ohdp_orchestration.jobs.openmetadata_sync import (
    openmetadata_dagster_sync_job,
    openmetadata_dbt_sync_job,
    openmetadata_seed_sync_job,
    openmetadata_snowflake_sync_job,
)
from ohdp_orchestration.schedules.cdc import (
    cdc_daily_schedule,
    cdc_monthly_schedule,
    cdc_weekly_schedule,
)
from ohdp_orchestration.schedules.cms import (
    cms_daily_schedule,
    cms_monthly_schedule,
    cms_weekly_schedule,
)
from ohdp_orchestration.schedules.cube_metrics_sync import (
    openmetadata_cube_metrics_sync_schedule,
)
from ohdp_orchestration.schedules.healthdata_gov import (
    healthdata_gov_daily_schedule,
    healthdata_gov_monthly_schedule,
    healthdata_gov_weekly_schedule,
)
from ohdp_orchestration.schedules.literature_dataset_sync import literature_dataset_sync_schedule
from ohdp_orchestration.schedules.literature_trending_sync import (
    literature_trending_sync_schedule,
)
from ohdp_orchestration.schedules.openaq import (
    openaq_daily_schedule,
    openaq_monthly_schedule,
    openaq_weekly_schedule,
)
from ohdp_orchestration.schedules.openmetadata_sync import (
    openmetadata_dagster_sync_schedule,
    openmetadata_dbt_sync_schedule,
    openmetadata_seed_sync_schedule,
    openmetadata_snowflake_sync_schedule,
)
from ohdp_orchestration.sensors.lakehouse_dbt import lakehouse_dbt_automation_sensor

# `load_defs` (not `load_from_defs_folder`) on purpose: the Docker image installs
# this package `--no-editable` (ADR-0004), so at runtime this file lives in
# site-packages with no project `pyproject.toml` next to it. `load_defs` only
# needs the module — but its default `project_root` autodetection still walks up
# for a pyproject and raises when there isn't one, so pass the package dir
# explicitly (it only affects code-reference links in the UI).
_project_root = Path(__file__).resolve().parent

defs = Definitions.merge(
    load_defs(_defs_module, project_root=_project_root),
    Definitions(
        assets=[
            literature_dataset_sync,
            literature_trending_sync,
            openmetadata_cube_metrics_sync,
            openmetadata_dagster_sync,
            openmetadata_dbt_sync,
            openmetadata_seed_sync,
            openmetadata_snowflake_sync,
            lakehouse_dbt_assets,
        ],
        resources={"dbt": DBT_RESOURCE},
        jobs=[
            cdc_daily_ingest_job,
            cdc_weekly_ingest_job,
            cdc_monthly_ingest_job,
            cms_daily_ingest_job,
            cms_weekly_ingest_job,
            cms_monthly_ingest_job,
            healthdata_gov_daily_ingest_job,
            healthdata_gov_weekly_ingest_job,
            healthdata_gov_monthly_ingest_job,
            literature_dataset_sync_job,
            literature_trending_sync_job,
            openaq_daily_ingest_job,
            openaq_weekly_ingest_job,
            openaq_monthly_ingest_job,
            openmetadata_cube_metrics_sync_job,
            openmetadata_dagster_sync_job,
            openmetadata_dbt_sync_job,
            openmetadata_seed_sync_job,
            openmetadata_snowflake_sync_job,
        ],
        schedules=[
            cdc_daily_schedule,
            cdc_weekly_schedule,
            cdc_monthly_schedule,
            cms_daily_schedule,
            cms_weekly_schedule,
            cms_monthly_schedule,
            healthdata_gov_daily_schedule,
            healthdata_gov_weekly_schedule,
            healthdata_gov_monthly_schedule,
            literature_dataset_sync_schedule,
            literature_trending_sync_schedule,
            openaq_daily_schedule,
            openaq_weekly_schedule,
            openaq_monthly_schedule,
            openmetadata_cube_metrics_sync_schedule,
            openmetadata_dagster_sync_schedule,
            openmetadata_dbt_sync_schedule,
            openmetadata_seed_sync_schedule,
            openmetadata_snowflake_sync_schedule,
        ],
        sensors=[lakehouse_dbt_automation_sensor],
    ),
)
