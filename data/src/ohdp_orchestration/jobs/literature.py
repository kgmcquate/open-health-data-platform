"""Job for the curated literature sync asset
(``ohdp_orchestration.assets.literature_sync``)."""

from __future__ import annotations

from dagster import AssetSelection, define_asset_job

from ohdp_orchestration.assets.literature_sync import literature_sync

literature_sync_job = define_asset_job(
    name="literature_sync_job",
    selection=AssetSelection.assets(literature_sync),
)
