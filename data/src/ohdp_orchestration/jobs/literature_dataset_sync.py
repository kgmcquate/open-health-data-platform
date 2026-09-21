"""Job for the per-dataset literature relevance sync asset
(``ohdp_orchestration.assets.literature_dataset_sync``)."""

from __future__ import annotations

from dagster import AssetSelection, define_asset_job

from ohdp_orchestration.assets.literature_dataset_sync import literature_dataset_sync

literature_dataset_sync_job = define_asset_job(
    name="literature_dataset_sync_job",
    selection=AssetSelection.assets(literature_dataset_sync),
)
