# The @dlt_assets body is built per instance; its `context` param is resolved
# by Dagster, not annotated here.
# mypy: disable-error-code="no-untyped-def, no-untyped-call, type-arg, arg-type"
"""HealthData.gov components — one instance per dataset.

* ``HealthDataGovDataset`` — one component **instance per dataset**
  (``datasets/<slug>/defs.yaml``). Its schema is the ``DatasetConfig`` contract
  plus two knobs. Each instance emits:

  - a **catalog asset** ``healthdata_gov/catalog/<raw_table>`` (unexecutable
    ``AssetSpec``, kind ``socrata``) — always;
  - a **table asset** ``healthdata_gov/<raw_table>`` (a ``@dlt_assets``-decorated
    dlt pipeline, ``deps=[catalog asset]``, kind ``dlt`` + whatever the
    destination actually is that run) — only when ``enabled: true``. The run
    itself goes through ``ohdp_orchestration.resources.dlt.DLT_RESOURCE`` — see
    that module for why a plain ``dlt.run()`` isn't safe here.

* ``HealthDataGovCadenceSchedules`` — one instance (``schedules/defs.yaml``).
  Builds exactly three asset jobs + schedules, one per cadence, selecting table
  assets by their ``ohdp/cadence`` tag. It never looks at the dataset files, so
  adding datasets never touches it.
"""

from __future__ import annotations

from dataclasses import dataclass

from dagster import (
    AssetKey,
    AssetMaterialization,
    AssetSelection,
    AssetSpec,
    DefaultScheduleStatus,
    Definitions,
    MetadataValue,
    ScheduleDefinition,
    TableColumn,
    TableSchema,
    define_asset_job,
)
from dagster.components import Component, ComponentLoadContext, Model, Resolvable
from dagster_dlt import DagsterDltResource, DagsterDltTranslator, dlt_assets
from dagster_dlt.translator import DltResourceTranslatorData

from ohdp_ingestion import naming
from ohdp_ingestion.healthdata_gov.config import Cadence, DatasetConfig
from ohdp_ingestion.healthdata_gov.source import build_pipeline, socrata_source
from ohdp_orchestration.resources.dlt import DLT_RESOURCE
from ohdp_shared import get_logger

log = get_logger(__name__)

_DOMAIN = "healthdata_gov"
_SOURCE = "healthdata_gov"
_RAW_NS = naming.namespace("raw", _SOURCE)  # "RAW.healthdata_gov" (ADR-0013)
_CADENCES: tuple[Cadence, ...] = ("daily", "weekly", "monthly")

# Must match warehouse/defs.yaml's `key_prefix` — there's no shared constant for
# it since the two components are independently configured, but the dbt
# translator's `get_asset_key` (warehouse/component.py) computes
# [prefix, database, schema, name] for dbt's `healthdata_gov` source nodes too,
# and that key needs to line up with `_warehouse_raw_spec()` below.
_WAREHOUSE_PREFIX = "warehouse"


@dataclass
class _TableTranslator(DagsterDltTranslator):
    """Grafts ``HealthDataGovDataset._table_spec()`` onto the spec
    ``DagsterDltTranslator`` derives from the dlt resource, rather than
    reimplementing key/deps/tags/metadata mapping from scratch — the base
    spec already carries the socrata-catalog dep, the cadence tag the
    schedules select on, and the advertised column schema. Left alone (from
    the base translator): ``automation_condition``, ``owners``, and
    ``kinds`` — kinds default to ``{"dlt", <actual destination>}``, which
    tracks dev (duckdb) vs. prod (snowflake) automatically.
    """

    spec: AssetSpec

    def get_asset_spec(self, data: DltResourceTranslatorData) -> AssetSpec:
        return super().get_asset_spec(data).replace_attributes(
            key=self.spec.key,
            deps=self.spec.deps,
            description=self.spec.description,
            group_name=self.spec.group_name,
        ).merge_attributes(
            tags=self.spec.tags,
            metadata=self.spec.metadata,
        )


class HealthDataGovDataset(Component, DatasetConfig, Resolvable):
    """One HealthData.gov dataset. Schema == ``DatasetConfig`` + ``group_name``."""

    group_name: str = _DOMAIN

    # --- keys ---------------------------------------------------------------
    @property
    def catalog_key(self) -> AssetKey:
        return AssetKey([_DOMAIN, "catalog", self.raw_table])

    @property
    def table_key(self) -> AssetKey:
        return AssetKey([_DOMAIN, self.raw_table])

    @property
    def warehouse_raw_key(self) -> AssetKey:
        """Best-effort label for where this table lives in the warehouse's RAW
        layer — `[prefix, "RAW", schema, table]`, via the same `ohdp_ingestion.
        naming` module the raw loader itself uses. This does **not** currently
        equal dbt's actual compiled source-node asset key: that key comes from
        whatever target `dbt/target/manifest.json` was last parsed against
        (`ci`/`local`, both DuckDB — see Dockerfile/ci.yml, neither ever
        `prod`), so dbt's source lands at e.g. `warehouse/ci_build/healthdata_gov/
        <table>` instead. Making these line up for real needs either reading the
        compiled manifest here instead of guessing, or baking the image's
        manifest with `--target prod` — neither is done yet.
        """
        return AssetKey(
            [
                _WAREHOUSE_PREFIX,
                naming.database("raw"),
                naming.schema("raw", _SOURCE),
                self.raw_table,
            ]
        )

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
            "publisher": self.publisher,
            "cadence": self.cadence,
            "enabled": self.enabled,
            "lands_in": (f"{_RAW_NS}.{self.raw_table}" if self.enabled else "(not ingested)"),
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
            group_name=f"{self.group_name}_catalog",
            description=self.description or self.name,
            metadata=metadata,
            tags={"ohdp/domain": _DOMAIN, "ohdp/enabled": str(self.enabled).lower()},
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
            "dagster/table_name": f"{_RAW_NS}.{self.raw_table}",
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
            group_name=self.group_name,
            description=(f"{self.name} — appended to {_RAW_NS}.{self.raw_table} by dlt."),
            metadata=metadata,
            # ohdp/cadence lives only on the table asset: it is what the cadence
            # schedules select on.
            tags={"ohdp/domain": _DOMAIN, "ohdp/cadence": self.cadence},
        )

    def _warehouse_raw_spec(self) -> AssetSpec:
        """Unexecutable RAW-layer label for this table (``warehouse_raw_key``),
        downstream of the dlt table. NOT currently the same asset dbt's
        `healthdata_gov` source resolves to in the live graph — see the
        docstring on `warehouse_raw_key`. `_table_asset()`'s op reports its
        materialization directly (see below); this spec never runs its own op.
        """
        return AssetSpec(
            key=self.warehouse_raw_key,
            deps=[self.table_key],
            group_name=f"{_WAREHOUSE_PREFIX}_raw",
            description=f"{self.name} — as dbt's `{_DOMAIN}` source sees it.",
            tags={"ohdp/domain": _WAREHOUSE_PREFIX, "ohdp/layer": "raw"},
            kinds={"snowflake"},
        )

    # --- defs ------------------------------------------------------------------
    def _table_asset(self):
        cfg = self
        translator = _TableTranslator(spec=self._table_spec())

        @dlt_assets(
            dlt_source=socrata_source(
                cfg.id,
                cfg.raw_table,
                incremental_cursor=cfg.incremental_cursor,
                row_limit=cfg.row_limit,
            ),
            dlt_pipeline=build_pipeline(pipeline_name=f"{_SOURCE}_{cfg.raw_table}", source=_SOURCE),
            name=cfg.raw_table,
            dagster_dlt_translator=translator,
            op_tags={"ohdp/cadence": cfg.cadence},
        )
        def _assets(context, dlt: DagsterDltResource):
            yield from dlt.run(context=context, loader_file_format="parquet")
            # `warehouse_raw_key` has no op of its own (`_warehouse_raw_spec` is
            # unexecutable) — report it materialized here, since this run is
            # exactly what freshens the table dbt's source reads.
            context.instance.report_runless_asset_event(
                AssetMaterialization(
                    asset_key=cfg.warehouse_raw_key,
                    description="Represents data copied into the raw layer.",
                )
            )

        return _assets

    def build_defs(self, context: ComponentLoadContext) -> Definitions:
        assets: list = [self._catalog_spec()]
        resources: dict = {}
        if self.enabled:
            assets.append(self._table_asset())
            assets.append(self._warehouse_raw_spec())
            resources["dlt"] = DLT_RESOURCE
        return Definitions(assets=assets, resources=resources)


class HealthDataGovCadenceSchedules(Component, Model, Resolvable):
    """The three cadence jobs + schedules. One instance, no coupling to datasets."""

    daily_cron: str = "0 7 * * *"
    weekly_cron: str = "0 7 * * 1"
    monthly_cron: str = "0 7 1 * *"
    # STOPPED until a deploy turns them on.
    default_status: str = "STOPPED"

    def build_defs(self, context: ComponentLoadContext) -> Definitions:
        status = (
            DefaultScheduleStatus.RUNNING
            if str(self.default_status).upper() == "RUNNING"
            else DefaultScheduleStatus.STOPPED
        )
        crons = {"daily": self.daily_cron, "weekly": self.weekly_cron, "monthly": self.monthly_cron}

        jobs = []
        schedules = []
        for cadence in _CADENCES:
            selection = AssetSelection.tag("ohdp/domain", _DOMAIN) & AssetSelection.tag(
                "ohdp/cadence", cadence
            )
            job = define_asset_job(
                name=f"healthdata_gov_{cadence}_ingest",
                selection=selection,
                description=f"HealthData.gov {cadence} ingestion bucket.",
                tags={"ohdp/domain": _DOMAIN, "ohdp/cadence": cadence},
            )
            jobs.append(job)
            schedules.append(
                ScheduleDefinition(
                    name=f"healthdata_gov_{cadence}_schedule",
                    job=job,
                    cron_schedule=crons[cadence],
                    default_status=status,
                )
            )
        return Definitions(jobs=jobs, schedules=schedules)
