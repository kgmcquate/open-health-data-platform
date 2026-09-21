"""Job for the trending-literature sync asset
(``ohdp_orchestration.assets.literature_trending_sync``)."""

from __future__ import annotations

from dagster import AssetSelection, define_asset_job

from ohdp_orchestration.assets.literature_trending_sync import literature_trending_sync

literature_trending_sync_job = define_asset_job(
    name="literature_trending_sync_job",
    selection=AssetSelection.assets(literature_trending_sync),
)
