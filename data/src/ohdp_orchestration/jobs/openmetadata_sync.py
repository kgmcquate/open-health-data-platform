"""One job per OpenMetadata sync asset (``ohdp_orchestration.assets.openmetadata_sync``)."""

from __future__ import annotations

from dagster import AssetSelection, define_asset_job

from ohdp_orchestration.assets.openmetadata_sync import (
    openmetadata_dagster_sync,
    openmetadata_snowflake_sync,
)

openmetadata_dagster_sync_job = define_asset_job(
    name="openmetadata_dagster_sync_job",
    selection=AssetSelection.assets(openmetadata_dagster_sync),
)

openmetadata_snowflake_sync_job = define_asset_job(
    name="openmetadata_snowflake_sync_job",
    selection=AssetSelection.assets(openmetadata_snowflake_sync),
)
