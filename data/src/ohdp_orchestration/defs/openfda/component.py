# The @dlt_assets body is built per instance; its `context` param is resolved
# by Dagster, not annotated here.
# mypy: disable-error-code="no-untyped-def, no-untyped-call, type-arg, arg-type"
"""``OpenFDADataset`` — one component **instance per openFDA endpoint**, the
openFDA binding of the same three-asset shape
``ohdp_orchestration.defs.cms.component.CMSDataset``/
``ohdp_orchestration.defs.openaq.component.OpenAQDataset`` give every other
raw source (ADR-0008, ADR-0018, ADR-0026). openFDA isn't Socrata, CMS or
OpenAQ, but it produces the identical asset graph, for the same reason:
lineage and the cadence jobs need the same three nodes regardless of what's
underneath.

Like CMS there's exactly one dlt-source shape here (a plain paginated REST
endpoint), so — like ``CMSDataset`` and unlike ``OpenAQDataset``'s two-resource
dispatch — there's no branching in ``_dlt_source``, just ``endpoint``/``search``
threaded straight through to ``openfda_source``. Like OpenAQ, openFDA isn't a
catalog of many independently-discoverable datasets, just a fixed, small set
of well-known endpoints, so ``datasets/defs.yaml`` is hand-maintained rather
than scraper-generated.

Each instance emits up to three assets:

* a **source asset** ``sources/openfda/<raw_table>`` — an unexecutable
  ``AssetSpec`` (kind ``openfda``) standing for the endpoint as openFDA
  publishes it. Always; never materialized.
* a **table asset** ``ingestion/openfda/<raw_table>`` — a ``@dlt_assets``
  pipeline, ``deps=[source asset]``, kind ``dlt`` + whatever the destination
  actually is that run. Only when ``enabled: true``.
* a **raw-layer asset** ``lakehouse/raw/openfda/<raw_table>`` — an unexecutable
  ``AssetSpec`` labelling the Iceberg table in the catalog, which receives a
  *runless* materialization event from the table asset's op. Only when
  ``enabled: true``.

Disabled datasets have only the source node, so the whole catalog stays
visible to the OpenMetadata Dagster ingestion; enabling one adds the two
downstream nodes without touching it.

The cadence asset jobs + schedules need no per-instance config, so they are
plain code rather than a component — see ``ohdp_orchestration.jobs.openfda``.
"""

from __future__ import annotations

from dataclasses import dataclass

from dagster import (
    AssetKey,
    AssetMaterialization,
    AssetsDefinition,
    AssetSpec,
    Definitions,
    MetadataValue,
)
from dagster.components import Component, ComponentLoadContext, Resolvable
from dagster_dlt import DagsterDltResource, DagsterDltTranslator, dlt_assets
from dagster_dlt.translator import DltResourceTranslatorData

from ohdp_ingestion import naming
from ohdp_ingestion.openfda import DatasetConfig, build_pipeline, openfda_source
from ohdp_orchestration.resources.dlt import DLT_RESOURCE
from ohdp_shared import get_logger

log = get_logger(__name__)

SOURCE = "openfda"

# Same prefixes as ohdp_orchestration.defs.cms/.openaq — kept identical so
# every source's assets read the same way in the Dagster UI and select the
# same way for OpenMetadata's Dagster ingestion.
SOURCES_PREFIX = "sources"
INGESTION_PREFIX = "ingestion"
LAKEHOUSE_PREFIX = "lakehouse"


@dataclass
class _TableTranslator(DagsterDltTranslator):
    """Grafts ``OpenFDADataset._table_spec()`` onto the spec
    ``DagsterDltTranslator`` derives from the dlt resource — see
    ``ohdp_orchestration.defs.cms.component._TableTranslator``, which this
    mirrors exactly."""

    spec: AssetSpec

    def get_asset_spec(self, data: DltResourceTranslatorData) -> AssetSpec:
        return (
            super()
            .get_asset_spec(data)
            .replace_attributes(
                key=self.spec.key,
                deps=self.spec.deps,
                description=self.spec.description,
                group_name=self.spec.group_name,
            )
            .merge_attributes(
                tags=self.spec.tags,
                metadata=self.spec.metadata,
            )
        )


class OpenFDADataset(Component, DatasetConfig, Resolvable):
    """One openFDA REST endpoint. Schema == ``DatasetConfig`` + ``group_name``."""

    group_name: str | None = None

    @property
    def _group(self) -> str:
        return self.group_name or SOURCE

    @property
    def _raw_namespace(self) -> str:
        """``"RAW.OPENFDA"`` (ADR-0013)."""
        return naming.namespace("raw", SOURCE)

    # --- keys ---------------------------------------------------------------
    @property
    def catalog_key(self) -> AssetKey:
        return AssetKey([SOURCES_PREFIX, SOURCE, self.raw_table])

    @property
    def table_key(self) -> AssetKey:
        return AssetKey([INGESTION_PREFIX, SOURCE, self.raw_table])

    def _lakehouse_raw_key(self, table: str) -> AssetKey:
        """Label for where a table lives in the raw layer — matches dbt's
        compiled source-node asset key (ADR-0019); see
        ``ohdp_orchestration.defs.cms.component`` for the identical logic on
        the CMS side."""
        return AssetKey(
            [
                LAKEHOUSE_PREFIX,
                naming.database("raw").lower(),
                naming.schema("raw", SOURCE).lower(),
                table.lower(),
            ]
        )

    @property
    def lakehouse_raw_key(self) -> AssetKey:
        return self._lakehouse_raw_key(self.raw_table)

    @property
    def _write_disposition_description(self) -> str:
        if self.incremental_cursor:
            return (
                f"merge on {self.primary_key}, cursor {self.incremental_cursor} "
                f"({self.incremental_lag_days}d rolling window -- see "
                "ohdp_ingestion.openfda.source)"
            )
        return "replace (no incremental_cursor/primary_key configured)"

    # --- specs ---------------------------------------------------------------
    def _catalog_spec(self) -> AssetSpec:
        metadata: dict[str, object] = {
            "openfda_endpoint": self.endpoint,
            "publisher": self.publisher,
            "cadence": self.cadence,
            "enabled": self.enabled,
            "lands_in": (
                f"{self._raw_namespace}.{self.raw_table}" if self.enabled else "(not ingested)"
            ),
            "write_disposition": self._write_disposition_description,
            "row_limit": self.row_limit,
        }
        if self.search:
            metadata["search"] = self.search
        if self.source_url:
            metadata["dagster/uri"] = MetadataValue.url(self.source_url)

        return AssetSpec(
            key=self.catalog_key,
            group_name=f"{SOURCES_PREFIX}_{self._group}",
            description=self.description or self.name,
            metadata=metadata,
            tags={"domain": SOURCE, "enabled": str(self.enabled).lower()},
            kinds={"openfda"},
        )

    def _table_spec(self) -> AssetSpec:
        metadata: dict[str, object] = {
            "dagster/table_name": f"{self._raw_namespace}.{self.raw_table}",
            "openfda_endpoint": self.endpoint,
            "write_disposition": self._write_disposition_description,
        }
        verb = "merges into" if self.incremental_cursor else "replaces"
        return AssetSpec(
            key=self.table_key,
            deps=[self.catalog_key],
            group_name=f"{INGESTION_PREFIX}_{self._group}",
            description=f"{self.name} — {verb} {self._raw_namespace}.{self.raw_table} by dlt.",
            metadata=metadata,
            # cadence lives only on the table asset: it is what the cadence
            # schedules select on.
            tags={"domain": SOURCE, "cadence": self.cadence},
        )

    def _lakehouse_raw_spec(self) -> AssetSpec:
        return AssetSpec(
            key=self.lakehouse_raw_key,
            deps=[self.table_key],
            group_name=f"{LAKEHOUSE_PREFIX}_raw",
            description=f"{self.name} — as dbt's `{SOURCE}` source sees it.",
            tags={"domain": LAKEHOUSE_PREFIX, "layer": "raw"},
            kinds={"iceberg"},
        )

    # --- defs ---------------------------------------------------------------
    def _table_asset(self) -> AssetsDefinition:
        cfg = self
        translator = _TableTranslator(spec=self._table_spec())

        @dlt_assets(
            dlt_source=openfda_source(
                cfg.endpoint,
                cfg.raw_table,
                search=cfg.search,
                row_limit=cfg.row_limit,
                incremental_cursor=cfg.incremental_cursor,
                primary_key=cfg.primary_key,
                incremental_lag_days=cfg.incremental_lag_days,
            ),
            dlt_pipeline=build_pipeline(pipeline_name=f"{SOURCE}_{cfg.raw_table}", source=SOURCE),
            name=cfg.raw_table,
            dagster_dlt_translator=translator,
            op_tags={"cadence": cfg.cadence},
        )
        def _assets(context, dlt: DagsterDltResource):
            # Mirrors defs.cms.component's table asset op — see that
            # module's docstring (via components.socrata) for why
            # table_names has to come off the run event rather than the
            # pipeline object afterward.
            for event in dlt.run(context=context, loader_file_format="parquet"):
                yield event
                raw_table_names = (event.metadata or {}).get("table_names")
                table_names = (
                    raw_table_names if isinstance(raw_table_names, list) else [cfg.raw_table]
                )
                for table_name in table_names:
                    context.instance.report_runless_asset_event(
                        AssetMaterialization(
                            asset_key=cfg._lakehouse_raw_key(table_name),
                            description="Represents data copied into the raw layer.",
                        )
                    )

        return _assets

    def build_defs(self, context: ComponentLoadContext) -> Definitions:
        assets: list = [self._catalog_spec()]
        resources: dict = {}
        if self.enabled:
            assets.append(self._table_asset())
            assets.append(self._lakehouse_raw_spec())
            resources["dlt"] = DLT_RESOURCE
        return Definitions(assets=assets, resources=resources)
