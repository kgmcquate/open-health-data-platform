# The @multi_asset body is built per instance; its `context` param is resolved
# by Dagster, not annotated here.
# mypy: disable-error-code="no-untyped-def, no-untyped-call, type-arg, arg-type"
"""HealthData.gov components — one instance per dataset.

* ``HealthDataGovDataset`` — one component **instance per dataset**
  (``datasets/<slug>/defs.yaml``). Its schema is the ``DatasetConfig`` contract
  plus two knobs. Each instance emits:

  - a **catalog asset** ``healthdata_gov/catalog/<raw_table>`` (unexecutable
    ``AssetSpec``, kind ``socrata``) — always;
  - a **table asset** ``healthdata_gov/<raw_table>`` (a ``dlt`` pipeline,
    ``deps=[catalog asset]``, kinds ``dlt`` + ``iceberg``) — only when
    ``enabled: true``.

* ``HealthDataGovCadenceSchedules`` — one instance (``schedules/defs.yaml``).
  Builds exactly three asset jobs + schedules, one per cadence, selecting table
  assets by their ``ohdp/cadence`` tag. It never looks at the dataset files, so
  adding datasets never touches it.
"""

from __future__ import annotations

from dagster import (
    AssetKey,
    AssetSelection,
    AssetSpec,
    DefaultScheduleStatus,
    Definitions,
    MaterializeResult,
    MetadataValue,
    ScheduleDefinition,
    TableColumn,
    TableSchema,
    define_asset_job,
    multi_asset,
)
from dagster.components import Component, ComponentLoadContext, Model, Resolvable

from ohdp_ingestion.healthdata_gov.config import Cadence, DatasetConfig
from ohdp_ingestion.healthdata_gov.source import build_pipeline, socrata_source
from ohdp_ingestion.iceberg import namespace
from ohdp_shared import get_logger

log = get_logger(__name__)

_DOMAIN = "healthdata_gov"
_SOURCE = "healthdata_gov"
_RAW_NS = namespace("raw", _SOURCE)  # "raw_healthdata_gov"
_CADENCES: tuple[Cadence, ...] = ("daily", "weekly", "monthly")


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
            description=(
                f"{self.name} — appended to Iceberg table {_RAW_NS}.{self.raw_table} by dlt."
            ),
            metadata=metadata,
            # ohdp/cadence lives only on the table asset: it is what the cadence
            # schedules select on.
            tags={"ohdp/domain": _DOMAIN, "ohdp/cadence": self.cadence},
            kinds={"dlt", "iceberg"},
        )

    # --- defs ------------------------------------------------------------------
    def _table_asset(self):
        spec = self._table_spec()
        key = spec.key
        cfg = self

        @multi_asset(specs=[spec], name=cfg.raw_table, op_tags={"ohdp/cadence": cfg.cadence})
        def _asset(context) -> MaterializeResult:
            pipeline = build_pipeline(
                pipeline_name=f"healthdata_gov_{cfg.raw_table}",
                source=_SOURCE,
            )
            source = socrata_source(
                cfg.id,
                cfg.raw_table,
                incremental_cursor=cfg.incremental_cursor,
                row_limit=cfg.row_limit,
            )
            info = pipeline.run(source)

            metadata: dict[str, object] = {"dlt/load_ids": MetadataValue.json(info.loads_ids)}
            try:
                norm = pipeline.last_trace.last_normalize_info
                if norm is not None and cfg.raw_table in norm.row_counts:
                    metadata["dlt/rows_appended"] = MetadataValue.int(
                        norm.row_counts[cfg.raw_table]
                    )
            except Exception as exc:  # noqa: BLE001 — metrics are best-effort
                context.log.warning("no dlt row metrics for %s: %s", cfg.raw_table, exc)
            try:
                from ohdp_ingestion.iceberg import load_catalog

                table = load_catalog().load_table(f"{_RAW_NS}.{cfg.raw_table}")
                snap = table.current_snapshot()
                arrow_schema = table.schema().as_arrow()
                metadata["dagster/column_schema"] = TableSchema(
                    columns=[
                        TableColumn(name=f.name, type=str(f.type))
                        for f in arrow_schema
                        if not f.name.startswith("_dlt")
                    ]
                )
                if snap is not None:
                    total = snap.summary.get("total-records")
                    if total is not None:
                        metadata["dagster/row_count"] = MetadataValue.int(int(total))
                    metadata["iceberg/snapshot_id"] = str(snap.snapshot_id)
            except Exception as exc:  # noqa: BLE001 — best-effort
                context.log.warning("no iceberg metadata for %s: %s", cfg.raw_table, exc)

            return MaterializeResult(asset_key=key, metadata=metadata)

        return _asset

    def build_defs(self, context: ComponentLoadContext) -> Definitions:
        assets: list = [self._catalog_spec()]
        if self.enabled:
            assets.append(self._table_asset())
        return Definitions(assets=assets)


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
