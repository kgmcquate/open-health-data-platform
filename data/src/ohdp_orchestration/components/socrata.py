# The @dlt_assets body is built per instance; its `context` param is resolved
# by Dagster, not annotated here.
# mypy: disable-error-code="no-untyped-def, no-untyped-call, type-arg, arg-type"
"""``SocrataDataset`` — one component **instance per Socrata dataset**.

The base class for every Socrata-hosted catalog's ingestion (ADR-0008 for the
config-driven shape, ADR-0018 for making it domain-agnostic). A concrete
subclass binds one :class:`~ohdp_ingestion.socrata.domain.SocrataDomain` and
adds nothing else — see
``ohdp_orchestration.defs.healthdata_gov.component.HealthDataGovDataset`` and
``ohdp_orchestration.defs.cdc.component.CDCDataset``.

Each instance (one ``datasets/<slug>/defs.yaml``, whose ``attributes`` block is
the ``DatasetConfig`` contract) emits up to three assets:

* a **source asset** ``sources/<source>/<raw_table>`` — an unexecutable
  ``AssetSpec`` (kind ``socrata``) standing for the dataset as published
  upstream. Always; never materialized.
* a **table asset** ``ingestion/<source>/<raw_table>`` — a ``@dlt_assets``
  pipeline, ``deps=[source asset]``, kind ``dlt`` + whatever the destination
  actually is that run. Only when ``enabled: true``. The run goes through
  ``ohdp_orchestration.resources.dlt.DLT_RESOURCE`` — see that module for why a
  plain ``dlt.run()`` isn't safe here.
* a **raw-layer asset** ``lakehouse/<catalog>/raw_<source>/<raw_table>`` — an
  unexecutable ``AssetSpec`` labelling the Iceberg table in the catalog, which
  receives a *runless* materialization event from the table asset's op for
  every table the load actually touched. Only when ``enabled: true``.

Disabled datasets have only the source node, so the whole catalog stays visible
to the OpenMetadata Dagster ingestion; enabling one adds the two downstream
nodes without touching it.

The cadence asset jobs + schedules need no per-instance config, so they are
plain code rather than components — see ``ohdp_orchestration.jobs.socrata``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from dagster import (
    AssetKey,
    AssetMaterialization,
    AssetsDefinition,
    AssetSpec,
    Definitions,
    MetadataValue,
    TableColumn,
    TableSchema,
)
from dagster.components import Component, ComponentLoadContext, Resolvable
from dagster_dlt import DagsterDltResource, DagsterDltTranslator, dlt_assets
from dagster_dlt.translator import DltResourceTranslatorData

from ohdp_ingestion import naming
from ohdp_ingestion.socrata import DatasetConfig, SocrataDomain, build_pipeline, socrata_source
from ohdp_orchestration.resources.dlt import DLT_RESOURCE
from ohdp_shared import get_logger

log = get_logger(__name__)

# Asset-key prefixes: unexecutable source-catalog specs live under `sources/`,
# the dlt-ingested table assets under `ingestion/` — kept distinct from
# `LAKEHOUSE_PREFIX` below and from each other so the graph reads
# source -> ingestion -> lakehouse left to right.
SOURCES_PREFIX = "sources"
INGESTION_PREFIX = "ingestion"

# Must match ohdp_orchestration.assets.lakehouse_dbt's `KEY_PREFIX` — there's
# no shared constant since the two modules are independent, but the dbt
# translator's `get_asset_key` (assets/lakehouse_dbt.py) computes
# [prefix, catalog, namespace, name] for each source's dbt source nodes too,
# and that key needs to line up with `_lakehouse_raw_spec()` below.
LAKEHOUSE_PREFIX = "lakehouse"


@dataclass
class _TableTranslator(DagsterDltTranslator):
    """Grafts ``SocrataDataset._table_spec()`` onto the spec
    ``DagsterDltTranslator`` derives from the dlt resource, rather than
    reimplementing key/deps/tags/metadata mapping from scratch — the base
    spec already carries the socrata-catalog dep, the cadence tag the
    schedules select on, and the advertised column schema. Left alone (from
    the base translator): ``automation_condition``, ``owners``, and
    ``kinds`` — kinds default to ``{"dlt", "filesystem"}``.
    """

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


class SocrataDataset(Component, DatasetConfig, Resolvable):
    """One Socrata dataset. Schema == ``DatasetConfig`` + ``group_name``.

    Subclasses set :attr:`SOCRATA` and nothing else.
    """

    # Not a pydantic field — pydantic excludes ClassVar, so this stays out of
    # the component's YAML schema. A subclass binds it; the base is abstract.
    SOCRATA: ClassVar[SocrataDomain]

    group_name: str | None = None

    # --- domain-derived names ----------------------------------------------
    @property
    def _source(self) -> str:
        return self.SOCRATA.source

    @property
    def _group(self) -> str:
        return self.group_name or self.SOCRATA.source

    @property
    def _raw_namespace(self) -> str:
        """e.g. ``"lakehouse.raw_healthdata_gov"`` (ADR-0019)."""
        return naming.namespace("raw", self._source)

    # --- keys ---------------------------------------------------------------
    @property
    def catalog_key(self) -> AssetKey:
        return AssetKey([SOURCES_PREFIX, self._source, self.raw_table])

    @property
    def table_key(self) -> AssetKey:
        return AssetKey([INGESTION_PREFIX, self._source, self.raw_table])

    def _lakehouse_raw_key(self, table: str) -> AssetKey:
        """Label for where a table lives in the catalog's raw layer —
        `[prefix, catalog, "raw_<source>", table]`, via the same
        `ohdp_ingestion.naming` module the raw loader itself uses, and
        matching dbt's actual compiled source-node asset key (ADR-0019).
        Takes an explicit table name (not always
        `self.raw_table`) because one dlt run can normalize nested JSON into
        several physical tables — `<raw_table>__<nested_field>`, one per
        array/object dlt flattens — and each gets its own key here; see
        `_table_asset()` below.
        """
        return AssetKey(
            [
                LAKEHOUSE_PREFIX,
                naming.catalog(),
                naming.schema("raw", self._source),
                table,
            ]
        )

    @property
    def lakehouse_raw_key(self) -> AssetKey:
        return self._lakehouse_raw_key(self.raw_table)

    # --- specs -------------------------------------------------------------
    def _advertised_schema(self) -> TableSchema | None:
        if not self.columns:
            return None
        return TableSchema(
            columns=[
                TableColumn(
                    name=c.name, type=c.type or "unknown", description=c.description or None
                )
                for c in self.columns
            ]
        )

    def _catalog_spec(self) -> AssetSpec:
        metadata: dict[str, object] = {
            "socrata_id": self.id,
            "socrata_domain": self.SOCRATA.domain,
            "publisher": self.publisher,
            "cadence": self.cadence,
            "enabled": self.enabled,
            "lands_in": (
                f"{self._raw_namespace}.{self.raw_table}" if self.enabled else "(not ingested)"
            ),
            "incremental_cursor": self.incremental_cursor or "(full replace each run)",
            "page_views_total": MetadataValue.int(self.page_views),
            "keywords": MetadataValue.json(self.keywords),
        }
        if self.source_url:
            metadata["dagster/uri"] = MetadataValue.url(self.source_url)
        schema = self._advertised_schema()
        if schema is not None:
            metadata["dagster/column_schema"] = schema

        return AssetSpec(
            key=self.catalog_key,
            group_name=f"{SOURCES_PREFIX}_{self._group}",
            description=self.description or self.name,
            metadata=metadata,
            tags={"domain": self._source, "enabled": str(self.enabled).lower()},
            kinds={"socrata"},
        )

    def _table_spec(self) -> AssetSpec:
        """The static (declared, not per-run) half of the table asset's spec.
        ``_TableTranslator`` grafts ``key``/``deps``/``description``/
        ``group_name``/``tags``/``metadata`` from this onto the spec
        ``dlt_assets`` derives from the dlt resource — so kinds (``dlt`` +
        the actual destination) stay dlt's own, dev-vs-prod-accurate call.
        """
        metadata: dict[str, object] = {
            "dagster/table_name": f"{self._raw_namespace}.{self.raw_table}",
            "socrata_id": self.id,
            "write_disposition": "append (raw is immutable history)",
            "fetch_cursor": self.incremental_cursor or "(full re-fetch each run)",
        }
        schema = self._advertised_schema()
        if schema is not None:
            metadata["dagster/column_schema"] = schema

        return AssetSpec(
            key=self.table_key,
            deps=[self.catalog_key],
            group_name=f"{INGESTION_PREFIX}_{self._group}",
            description=(
                f"{self.name} — appended to {self._raw_namespace}.{self.raw_table} by dlt."
            ),
            metadata=metadata,
            # cadence lives only on the table asset: it is what the cadence
            # schedules select on.
            tags={"domain": self._source, "cadence": self.cadence},
        )

    def _lakehouse_raw_spec(self) -> AssetSpec:
        """Unexecutable raw-layer label for this table (``lakehouse_raw_key``),
        downstream of the dlt table — the same key dbt's source node resolves
        to. `_table_asset()`'s op reports its materialization directly (see
        below); this spec never runs its own op.
        """
        return AssetSpec(
            key=self.lakehouse_raw_key,
            deps=[self.table_key],
            group_name=f"{LAKEHOUSE_PREFIX}_raw",
            description=f"{self.name} — as dbt's `{self._source}` source sees it.",
            tags={"domain": LAKEHOUSE_PREFIX, "layer": "raw"},
            kinds={"iceberg"},
        )

    # --- defs ---------------------------------------------------------------
    def _table_asset(self) -> AssetsDefinition:
        cfg = self
        translator = _TableTranslator(spec=self._table_spec())

        @dlt_assets(
            dlt_source=socrata_source(
                cfg.SOCRATA,
                cfg.id,
                cfg.raw_table,
                incremental_cursor=cfg.incremental_cursor,
                row_limit=cfg.row_limit,
                columns=cfg.columns,
            ),
            dlt_pipeline=build_pipeline(
                pipeline_name=f"{cfg._source}_{cfg.raw_table}", source=cfg._source
            ),
            name=cfg.raw_table,
            dagster_dlt_translator=translator,
            op_tags={"cadence": cfg.cadence},
        )
        def _assets(context, dlt: DagsterDltResource):
            # dlt normalizes nested JSON (arrays/objects) into their own child
            # tables — `<raw_table>__<nested_field>`, dlt's own naming scheme —
            # so one run can freshen several physical tables, not just
            # `raw_table` itself. `CustomDagsterDltResource.extract_resource_metadata`
            # (ohdp_orchestration/resources/dlt.py) already discovers them
            # (`metadata["table_names"]`) at the one point that's actually
            # reliable — inside the run, right after `dlt_pipeline.load()`.
            # Re-deriving it here from the pipeline object afterward silently
            # comes back empty (confirmed empirically: dlt's live schema isn't
            # queryable the same way once `_run`'s extract/normalize/load
            # sequence has returned), so read it back off the event instead.
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
