"""The Iceberg lake helpers + the custom dbt writer plugin, against a local
SqlCatalog (SQLite + a tmp warehouse dir) — the same pyiceberg API the Snowflake
Horizon Catalog serves in prod (ADR-0011)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest


@pytest.fixture
def local_lake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("OHDP_ICEBERG_CATALOG_URI", "")
    monkeypatch.setenv("OHDP_ICEBERG_LOCAL_CATALOG_PATH", str(tmp_path / "cat.db"))
    monkeypatch.setenv("OHDP_ICEBERG_LOCAL_WAREHOUSE", str(tmp_path / "wh"))
    import ohdp_ingestion.iceberg as ice
    from ohdp_shared.settings import Settings

    monkeypatch.setattr(ice, "settings", Settings())
    return ice


def _target_config(path: Path, ident: str, **cfg: Any) -> Any:
    return SimpleNamespace(
        config=SimpleNamespace(get=lambda k, d=None: cfg.get(k, d)),
        relation=SimpleNamespace(identifier=ident),
        location=SimpleNamespace(path=str(path), format="parquet"),
    )


def _write(path: Path, table: pa.Table) -> Path:
    pq.write_table(table, path)
    return path


def test_namespaces(local_lake: Any) -> None:
    assert local_lake.namespace("raw", "healthdata_gov") == "raw_healthdata_gov"
    assert local_lake.namespace("clean", "cdc") == "clean_cdc"
    assert local_lake.namespace("curated") == "core"
    assert local_lake.namespace("curated", "respiratory") == "mart_respiratory"


def test_local_catalog_roundtrip(local_lake: Any) -> None:
    cat = local_lake.load_catalog()
    cat.create_namespace_if_not_exists("raw_x")
    data = pa.table({"id": ["a", "b"], "v": [1, 2]})
    table = cat.create_table("raw_x.t", schema=data.schema)
    table.append(data)
    assert table.scan().to_arrow().num_rows == 2


def test_plugin_upsert_with_schema_evolution(local_lake: Any, tmp_path: Path) -> None:
    from ohdp_ingestion.dbt.iceberg import Plugin

    plugin = Plugin(name="iceberg", plugin_config={})
    opts = {
        "iceberg_namespace": "clean_x",
        "iceberg_strategy": "upsert",
        "unique_key": "socrata_id",
    }

    plugin.store(
        _target_config(
            _write(tmp_path / "s1.parquet", pa.table({"socrata_id": ["a", "b"], "v": [1, 2]})),
            "clean_demo",
            **opts,
        )
    )
    plugin.store(
        _target_config(
            _write(
                tmp_path / "s2.parquet",
                pa.table({"socrata_id": ["a", "c"], "v": [10, 3], "extra": ["x", "y"]}),
            ),
            "clean_demo",
            **opts,
        )
    )

    rows = local_lake.load_catalog().load_table("clean_x.clean_demo").scan().to_arrow().to_pydict()
    assert sorted(zip(rows["socrata_id"], rows["v"], strict=True)) == [
        ("a", 10),
        ("b", 2),
        ("c", 3),
    ]
    assert "extra" in rows


def test_plugin_overwrite(local_lake: Any, tmp_path: Path) -> None:
    from ohdp_ingestion.dbt.iceberg import Plugin

    plugin = Plugin(name="iceberg", plugin_config={})
    for values in ([1, 2, 3], [9]):
        table = pa.table({"id": [str(v) for v in values], "v": values})
        plugin.store(
            _target_config(
                _write(tmp_path / f"o{len(values)}.parquet", table), "m", iceberg_namespace="core"
            )
        )
    got = local_lake.load_catalog().load_table("core.m").scan().to_arrow().to_pydict()
    assert got["v"] == [9]
