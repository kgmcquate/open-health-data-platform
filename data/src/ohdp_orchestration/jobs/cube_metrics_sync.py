"""Job for the Cube -> OpenMetadata Metric sync asset
(``ohdp_orchestration.assets.cube_metrics_sync``)."""

from __future__ import annotations

from dagster import AssetSelection, define_asset_job

from ohdp_orchestration.assets.cube_metrics_sync import openmetadata_cube_metrics_sync

openmetadata_cube_metrics_sync_job = define_asset_job(
    name="openmetadata_cube_metrics_sync_job",
    selection=AssetSelection.assets(openmetadata_cube_metrics_sync),
)
