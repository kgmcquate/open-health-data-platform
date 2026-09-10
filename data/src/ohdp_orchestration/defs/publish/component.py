# mypy: disable-error-code="no-untyped-def, no-untyped-call, type-arg, arg-type"
"""Publish step: curated Iceberg -> a DuckDB snapshot in Spaces (ADR-0010 / 0002).

**Defined, not wired.** There is no curated data to publish yet (M0), and the
replica-side pull is M1. This lays down the interface: one asset that reads the
``core`` and ``mart_*`` namespaces via the catalog, writes
``warehouse-{ts}.duckdb``, uploads it and moves the ``current.json`` pointer.

Turn it on by adding it to a job/schedule downstream of the marts once they exist.
"""

from __future__ import annotations

import datetime as dt
import json
import tempfile
from pathlib import Path

from dagster import Definitions, MaterializeResult, MetadataValue, asset
from dagster.components import Component, ComponentLoadContext, Model, Resolvable

from ohdp_shared import get_logger
from ohdp_shared.settings import settings

log = get_logger(__name__)

_CURATED_NAMESPACE_PREFIXES = ("core", "mart_")


class WarehousePublish(Component, Model, Resolvable):
    """Assemble the serving DuckDB snapshot from the curated Iceberg layer."""

    group_name: str = "publish"
    snapshot_prefix: str = "snapshots"

    def build_defs(self, context: ComponentLoadContext) -> Definitions:
        group = self.group_name
        prefix = self.snapshot_prefix

        @asset(name="warehouse_snapshot", group_name=group, kinds={"duckdb", "iceberg"})
        def warehouse_snapshot(context) -> MaterializeResult:
            import duckdb

            from ohdp_ingestion.iceberg import load_catalog

            catalog = load_catalog()
            namespaces = [
                ns[0]
                for ns in catalog.list_namespaces()
                if ns and ns[0].startswith(_CURATED_NAMESPACE_PREFIXES)
            ]
            if not namespaces:
                context.log.warning("no curated namespaces yet; nothing to publish")
                return MaterializeResult(metadata={"tables": 0})

            ts = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
            out = Path(tempfile.mkdtemp()) / f"warehouse-{ts}.duckdb"
            con = duckdb.connect(str(out))
            written = 0
            for ns in namespaces:
                con.execute(f'CREATE SCHEMA IF NOT EXISTS "{ns}"')
                for _, table_name in catalog.list_tables(ns):
                    arrow = catalog.load_table(f"{ns}.{table_name}").scan().to_arrow()  # noqa: F841
                    con.execute(f'CREATE TABLE "{ns}"."{table_name}" AS SELECT * FROM arrow')
                    written += 1
            con.close()

            key = f"{prefix}/{out.name}"
            self._upload(out, key)
            self._upload_pointer(
                {"current": key, "published_at": ts, "tables": written}, f"{prefix}/current.json"
            )
            return MaterializeResult(
                metadata={
                    "tables": written,
                    "snapshot": MetadataValue.text(key),
                    "namespaces": MetadataValue.json(namespaces),
                }
            )

        return Definitions(assets=[warehouse_snapshot])

    # --- Spaces I/O -------------------------------------------------------
    def _s3(self):
        import boto3

        return boto3.client(
            "s3",
            endpoint_url=settings.spaces_endpoint_url or None,
            aws_access_key_id=settings.spaces_access_key_id or None,
            aws_secret_access_key=settings.spaces_secret_access_key or None,
            region_name=settings.spaces_region,
        )

    def _upload(self, path: Path, key: str) -> None:
        self._s3().upload_file(str(path), settings.spaces_bucket, key)

    def _upload_pointer(self, doc: dict, key: str) -> None:
        self._s3().put_object(
            Bucket=settings.spaces_bucket,
            Key=key,
            Body=json.dumps(doc).encode(),
            ContentType="application/json",
        )
