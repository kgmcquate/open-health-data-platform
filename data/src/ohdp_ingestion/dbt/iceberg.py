# dbt-duckdb's BasePlugin is untyped.
# mypy: disable-error-code="no-untyped-def, no-untyped-call, import-untyped, misc"
"""A dbt-duckdb plugin that reads **and writes** Iceberg tables (ADR-0010).

dbt-duckdb's built-in ``iceberg`` plugin is read-only and its ``external``
materialization only writes Parquet/CSV. This plugin adds Iceberg writes so
``clean`` / ``core`` / ``mart`` models can land in the lake, with ``upsert`` for
``incremental`` models.

Register in ``profiles.yml``::

    plugins:
      - module: ohdp_ingestion.dbt.iceberg
        alias: iceberg

Use in a model::

    {{ config(
        materialized="external",
        plugin="iceberg",
        options={"iceberg_namespace": "clean_healthdata_gov",
                 "iceberg_strategy": "upsert", "unique_key": "socrata_id"}
    ) }}

The ``external`` materialization still writes a scratch Parquet at ``location``
first (point it at a local tmp dir); this plugin then commits it to Iceberg. The
Iceberg table is the durable artifact — the publish step and any cross-run or
external reader go through the catalog.
"""

from __future__ import annotations

import warnings
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from dbt.adapters.duckdb.plugins import BasePlugin
from dbt.adapters.duckdb.utils import SourceConfig, TargetConfig

_VALID_STRATEGIES = ("overwrite", "append", "upsert")


def _align(data: pa.Table, schema: pa.Schema) -> pa.Table:
    """Project `data` onto `schema`: add missing columns as nulls, drop extras,
    order to match. Callers evolve the table first so `schema` is the superset."""
    cols = {}
    for field in schema:
        cols[field.name] = (
            data.column(field.name).cast(field.type)
            if field.name in data.column_names
            else pa.nulls(data.num_rows, field.type)
        )
    return pa.table(cols, schema=schema)


class Plugin(BasePlugin):
    def initialize(self, config: dict[str, Any]) -> None:
        # Catalog comes from ohdp_shared settings (Polaris in prod, local SqlCatalog
        # in dev). dbt-duckdb merges the profile's `settings:` block (s3 creds for
        # httpfs) into this dict, so only treat it as an override when it names a
        # pyiceberg catalog explicitly (`iceberg_catalog` sub-dict).
        self._override: dict[str, Any] = (config or {}).get("iceberg_catalog") or {}
        self._catalog: Any = None

    def _catalog_(self) -> Any:
        if self._catalog is None:
            if self._override.get("uri"):
                from pyiceberg.catalog import load_catalog as _pyiceberg_load

                name = self._override.pop("name", "ohdp")
                self._catalog = _pyiceberg_load(name, **self._override)
            else:
                from ohdp_ingestion.iceberg import load_catalog as _ohdp_load

                self._catalog = _ohdp_load()
        return self._catalog

    # --- read (sources / ref via `plugin`) --------------------------------
    def load(self, source_config: SourceConfig):
        ns = source_config.get("iceberg_namespace") or source_config.schema
        ident = source_config.get("iceberg_table") or source_config.identifier
        table = self._catalog_().load_table(f"{ns}.{ident}")
        scan_kwargs = {
            k: source_config[k]
            for k in ("row_filter", "selected_fields", "snapshot_id", "limit")
            if k in source_config
        }
        return table.scan(**scan_kwargs).to_arrow()

    def default_materialization(self) -> str:
        return "view"

    # --- write (models) --------------------------------------------------
    def store(self, target_config: TargetConfig) -> None:
        cfg = target_config.config
        ns = cfg.get("iceberg_namespace")
        if not ns:
            raise ValueError("iceberg model needs options.iceberg_namespace")
        strategy = cfg.get("iceberg_strategy", "overwrite")
        if strategy not in _VALID_STRATEGIES:
            raise ValueError(f"iceberg_strategy must be one of {_VALID_STRATEGIES}")

        table_name = target_config.relation.identifier
        table_id = f"{ns}.{table_name}"
        if target_config.location is None:
            raise ValueError("iceberg plugin requires a `location` (scratch parquet path)")
        data = pq.read_table(target_config.location.path)

        catalog = self._catalog_()
        catalog.create_namespace_if_not_exists(ns)

        from pyiceberg.exceptions import NoSuchTableError

        created = False
        try:
            table = catalog.load_table(table_id)
        except NoSuchTableError:
            table = catalog.create_table(table_id, schema=data.schema)
            created = True

        schema_grew = not set(data.schema.names) <= set(table.schema().column_names)

        # Evolve the table to the superset of columns, then reload a fresh handle
        # and project the incoming data onto that schema so every write path casts.
        with table.update_schema() as update:
            update.union_by_name(data.schema)
        table = catalog.load_table(table_id)
        data = _align(data, table.schema().as_arrow())

        if strategy == "append":
            table.append(data)
        elif strategy == "upsert":
            unique_key = cfg.get("unique_key")
            if not unique_key:
                raise ValueError("iceberg_strategy='upsert' needs a unique_key")
            join_cols = [unique_key] if isinstance(unique_key, str) else list(unique_key)
            if schema_grew and not created and len(table.scan().to_arrow()) > 0:
                # pyiceberg's upsert reads matched rows via a batch reader that
                # does not backfill columns added after a data file was written.
                # Rewrite the existing data under the evolved schema first.
                table.overwrite(table.scan().to_arrow())
                table = catalog.load_table(table_id)
            table.upsert(data, join_cols=join_cols)
        else:  # overwrite
            with warnings.catch_warnings():
                # "Delete operation did not match any records" on a fresh table.
                warnings.filterwarnings("ignore", message="Delete operation did not match")
                table.overwrite(data)
