# dlt's LoadInfo/Pipeline internals are untyped; DagsterDltResource's own
# methods we override carry the same relaxation upstream.
# mypy: disable-error-code="no-untyped-def, no-untyped-call, type-arg"
"""A ``DagsterDltResource`` extended for what every ``@dlt_assets`` here needs.

* **Metadata** — ``extract_resource_metadata`` adds child table names, the
  remote path each table's load jobs landed in, and dlt's own ``sources``
  pipeline state, on top of what the base resource already surfaces (row
  counts, column schema, job list).
* **A quiet-run guard** — ``_run`` extracts and normalizes before deciding
  whether to load. A resource with ``write_disposition="replace"`` that
  legitimately extracts zero new rows this run (upstream published nothing
  new) must leave the destination table alone, not truncate it. Plain
  ``pipeline.run()`` does *not* do this on its own — confirmed empirically,
  not assumed — because normalize still produces a load package for a
  ``replace`` table's schema even with no data rows, and ``load()`` truncates
  on it regardless. So on a quiet run this skips ``load()`` (and yields no
  ``MaterializeResult``); ``dlt_assets`` builds its op with ``can_subset=True``,
  which makes every asset's output optional, so Dagster reports the asset as
  "not materialized this run" rather than failing.

One instance (``DLT_RESOURCE``) is shared as the ``"dlt"`` resource by every
``@dlt_assets``-decorated component. ``Definitions.merge`` (what stitches
every component's ``build_defs`` together) requires the *same* resource
object across every merged ``Definitions``, not just an equal one — so this
must stay a singleton, not one instance per component.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from dagster import (
    AssetExecutionContext,
    AssetMaterialization,
    MaterializeResult,
    MetadataValue,
    OpExecutionContext,
)
from dagster_dlt import DagsterDltResource
from dagster_dlt.dlt_event_iterator import DltEventType
from dagster_dlt.translator import DagsterDltTranslator, DltResourceTranslatorData
from dlt.common.pipeline import LoadInfo
from dlt.extract.resource import DltResource
from dlt.extract.source import DltSource
from dlt.pipeline.pipeline import Pipeline


def _table_paths(load_info: LoadInfo) -> dict[str, str]:
    """table name -> remote folder its load jobs landed in, for one run.

    Only file-staging destinations (Snowflake via internal stage, Spaces/S3,
    ...) populate ``remote_url`` on a job; skip jobs that don't rather than
    error.
    """
    table_paths: dict[str, str] = {}
    for _, load_metrics in load_info.metrics.items():
        for load_metric in load_metrics:
            for _, job_metrics in load_metric["job_metrics"].items():
                if job_metrics.table_name == "_dlt_pipeline_state":
                    continue
                if not job_metrics.remote_url:
                    continue
                table_paths[job_metrics.table_name] = Path(job_metrics.remote_url).parent.as_posix()
    return table_paths


class CustomDagsterDltResource(DagsterDltResource):
    def extract_resource_metadata(
        self,
        context: OpExecutionContext | AssetExecutionContext,
        resource: DltResource,
        load_info: LoadInfo,
        dlt_pipeline: Pipeline,
    ) -> dict[str, Any]:
        normalized_table_name = dlt_pipeline.default_schema.naming.normalize_table_identifier(
            str(resource.table_name)
        )
        child_table_names = [
            name
            for name in dlt_pipeline.default_schema.data_table_names()
            if name.startswith(f"{normalized_table_name}__")
        ]

        metadata = dict(
            super().extract_resource_metadata(context, resource, load_info, dlt_pipeline)
        )
        metadata["table_names"] = [normalized_table_name, *child_table_names]
        metadata["table_paths"] = MetadataValue.json(_table_paths(load_info))
        metadata["sources_state"] = MetadataValue.json(load_info.pipeline.state.get("sources", {}))
        return metadata

    def _run(
        self,
        context: OpExecutionContext | AssetExecutionContext,
        dlt_source: DltSource,
        dlt_pipeline: Pipeline,
        dagster_dlt_translator: DagsterDltTranslator,
        **kwargs: Any,
    ) -> Iterator[DltEventType]:
        asset_key_dlt_source_resource_mapping = {
            dagster_dlt_translator.get_asset_spec(
                DltResourceTranslatorData(resource=dlt_source_resource, pipeline=dlt_pipeline)
            ).key: dlt_source_resource
            for dlt_source_resource in dlt_source.selected_resources.values()
        }

        if context.is_subset:
            asset_key_dlt_source_resource_mapping = {
                asset_key: asset_dlt_source_resource
                for (
                    asset_key,
                    asset_dlt_source_resource,
                ) in asset_key_dlt_source_resource_mapping.items()
                if asset_key in context.selected_asset_keys
            }
            dlt_source = dlt_source.with_resources(
                *[
                    dlt_source_resource.name
                    for dlt_source_resource in asset_key_dlt_source_resource_mapping.values()
                    if dlt_source_resource
                ]
            )

        # Same local-state reset the base resource does before a run — see its
        # docstring: restore_from_destination means it's always safe to drop
        # local state since it's rebuilt from the destination.
        if dlt_pipeline.config.restore_from_destination:
            dlt_pipeline.drop()
        else:
            dlt_pipeline.drop_pending_packages()

        dlt_pipeline.extract(dlt_source, **kwargs)
        normalize_info = dlt_pipeline.normalize()

        naming = dlt_pipeline.default_schema.naming
        new_rows = sum(
            normalize_info.row_counts.get(
                naming.normalize_table_identifier(str(resource.table_name)), 0
            )
            for resource in asset_key_dlt_source_resource_mapping.values()
        )
        if new_rows == 0:
            return

        load_info = dlt_pipeline.load()
        load_info.raise_on_failed_jobs()

        has_asset_def = bool(context and context.has_assets_def)
        for asset_key, dlt_source_resource in asset_key_dlt_source_resource_mapping.items():
            metadata = self.extract_resource_metadata(
                context, dlt_source_resource, load_info, dlt_pipeline
            )
            if has_asset_def:
                yield MaterializeResult(asset_key=asset_key, metadata=metadata)
            else:
                yield AssetMaterialization(asset_key=asset_key, metadata=metadata)


DLT_RESOURCE = CustomDagsterDltResource()
