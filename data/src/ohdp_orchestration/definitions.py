"""The single Definitions object for the code location (ARCHITECTURE.md §7).

Everything the daemon and webserver see is assembled here. Keep this file an
assembly point only — no asset bodies, no business logic.

Defs come from two places, merged together:

* ``ohdp_orchestration.defs`` is autoloaded: every Dagster component
  discovered under it (currently the config-driven HealthData.gov ingestion)
  is merged in automatically.
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
from ohdp_orchestration.assets.openmetadata_sync import (
    openmetadata_dagster_sync,
    openmetadata_dbt_sync,
    openmetadata_snowflake_sync,
)
from ohdp_orchestration.assets.snowflake_dbt import DBT_RESOURCE, snowflake_dbt_assets
from ohdp_orchestration.jobs.openmetadata_sync import (
    openmetadata_dagster_sync_job,
    openmetadata_dbt_sync_job,
    openmetadata_snowflake_sync_job,
)
from ohdp_orchestration.schedules.openmetadata_sync import (
    openmetadata_dagster_sync_schedule,
    openmetadata_dbt_sync_schedule,
    openmetadata_snowflake_sync_schedule,
)
from ohdp_orchestration.sensors.snowflake_dbt import snowflake_dbt_automation_sensor

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
            openmetadata_dagster_sync,
            openmetadata_dbt_sync,
            openmetadata_snowflake_sync,
            snowflake_dbt_assets,
        ],
        resources={"dbt": DBT_RESOURCE},
        jobs=[
            openmetadata_dagster_sync_job,
            openmetadata_dbt_sync_job,
            openmetadata_snowflake_sync_job,
        ],
        schedules=[
            openmetadata_dagster_sync_schedule,
            openmetadata_dbt_sync_schedule,
            openmetadata_snowflake_sync_schedule,
        ],
        sensors=[snowflake_dbt_automation_sensor],
    ),
)
