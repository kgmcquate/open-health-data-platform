"""The raw loader writes Iceberg tables into S3, catalogued through Horizon, in
prod (ADR-0019). Socrata is stubbed, and so is the *location* of the lakehouse:
a temp directory instead of S3, and a SQLite pyiceberg catalog instead of
Snowflake's REST endpoint. Everything between — dlt's filesystem destination,
the upper-casing naming convention, pyiceberg's writer, schema evolution,
``replace``'s truncate, nested-array flattening — is the real production path,
so these tests cover the Iceberg write itself rather than a SQL stand-in.

Exercises ``CustomDagsterDltResource`` (the actual production code path —
``ohdp_orchestration.components.socrata`` runs every table asset through it)
rather than a parallel test-only reimplementation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import dlt
import pytest
from dagster import (
    AssetKey,
    DagsterInstance,
    ExecuteInProcessResult,
    materialize,
)
from dagster_dlt import dlt_assets

from ohdp_ingestion import naming
from ohdp_ingestion.healthdata_gov import HEALTHDATA_GOV
from ohdp_ingestion.socrata import ColumnSpec, build_pipeline, socrata_source
from ohdp_orchestration.resources.dlt import CustomDagsterDltResource


@pytest.fixture
def lake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the lakehouse at a temp directory and a SQLite pyiceberg catalog.

    Both substitutions are configuration, not code paths: ``get_catalog``
    builds a real ``pyiceberg`` catalog either way (dlt supports ``sql`` and
    ``rest``), and the filesystem destination writes the same Parquet + Iceberg
    metadata to a local path as it does to ``s3://``.
    """
    root = tmp_path / "lake"
    root.mkdir()
    # Create the SQLite catalog's own bookkeeping tables up front. pyiceberg
    # does it lazily on first use, and dlt loads tables in parallel workers, so
    # two of them racing to bootstrap the same SQLite file fails with "table
    # iceberg_tables already exists". Purely an artifact of the test catalog —
    # Horizon has nothing to bootstrap.
    _load_test_catalog(root)
    # dlt keeps pipeline state (the incremental cursor) here.
    monkeypatch.setenv("DLT_DATA_DIR", str(tmp_path / "dlt"))

    import ohdp_ingestion.socrata.source as src

    monkeypatch.setattr(src, "_destination", lambda: _local_destination(root))
    monkeypatch.setattr(src, "configure_catalog", lambda: _local_catalog(root))
    return root


def _local_destination(root: Path) -> Any:
    return dlt.destinations.filesystem(bucket_url=root.as_uri())


def _catalog_config(root: Path) -> dict[str, Any]:
    """pyiceberg kwargs for the test catalog. `root` is the lake directory the
    `lake` fixture made; the catalog file sits beside it."""
    return {
        "type": "sql",
        "uri": f"sqlite:///{root.parent / 'catalog.db'}",
        # dlt passes each table an explicit location under the destination's
        # bucket_url, so this only has to be a valid default — but pyiceberg
        # requires it either way.
        "warehouse": root.as_uri(),
    }


def _load_test_catalog(root: Path) -> Any:
    from pyiceberg.catalog import load_catalog

    return load_catalog(naming.database("raw"), **_catalog_config(root))


def _local_catalog(root: Path) -> None:
    # Same upper-casing convention production uses — the table names these
    # tests assert on depend on it.
    dlt.config["schema.naming"] = "ohdp_ingestion.sql_upper"
    dlt.config["iceberg_catalog.iceberg_catalog_name"] = naming.database("raw")
    dlt.config["iceberg_catalog.iceberg_catalog_type"] = "sql"
    dlt.config["iceberg_catalog.iceberg_catalog_config"] = _catalog_config(root)


def _rows(root: Path, source: str, table: str) -> dict[str, Any]:
    """Read the committed Iceberg table back through the catalog.

    Deliberately pyiceberg rather than dlt's dataset API: that one reads
    through DuckDB's `iceberg` extension, which turns a data assertion into a
    test of a second engine (and of its ability to download an extension at
    run time). What these tests are checking is what the loader *committed*,
    which is exactly what the catalog hands back.
    """
    catalog = _load_test_catalog(root)
    # dlt normalizes every identifier through `ohdp_ingestion.sql_upper`, so
    # the committed table is `DEMO`, not `demo` — see that module for why the
    # lakehouse is upper case throughout.
    arrow_table = (
        catalog.load_table((naming.schema("raw", source), table.upper())).scan().to_arrow()
    )
    # Keys come back upper case (see the note above). Lower them here so the
    # assertions below read as statements about the *data*; the casing itself
    # is asserted once, in `test_identifiers_are_upper_cased`.
    return {name.lower(): values for name, values in arrow_table.to_pydict().items()}


def _stub_socrata(monkeypatch: pytest.MonkeyPatch, pages: list[list[dict[str, Any]]]) -> None:
    """Serve `pages` in order, then empty — the loop's stop condition.

    Patched on dlt's `requests` helper module itself, which is the same object
    the loader holds, rather than through the loader's re-export of it.
    """
    from dlt.sources.helpers import requests

    remaining = list(pages)

    class _Resp:
        def __init__(self, payload: list[dict[str, Any]]) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> list[dict[str, Any]]:
            return self._payload

    def _get(url: str, params: dict[str, Any], headers: dict[str, str]) -> _Resp:
        return _Resp(remaining.pop(0) if remaining else [])

    monkeypatch.setattr(requests, "get", _get)


def _load(
    *,
    resource_id: str,
    table_name: str,
    source: str = "healthdata_gov",
    incremental_cursor: str | None = "socrata_updated_at",
    columns: list[ColumnSpec] | None = None,
) -> ExecuteInProcessResult:
    """Materialize one table asset the same way the component does, through
    ``CustomDagsterDltResource``."""

    @dlt_assets(
        dlt_source=socrata_source(
            HEALTHDATA_GOV,
            resource_id,
            table_name,
            incremental_cursor=incremental_cursor,
            columns=columns,
        ),
        dlt_pipeline=build_pipeline(pipeline_name=f"{source}_{table_name}", source=source),
        name=table_name,
    )
    # Dagster identity-checks this annotation against AssetExecutionContext, and
    # this module's `from __future__ import annotations` would stringify it, so
    # `context` has to stay unannotated.
    def _assets(context, dlt: CustomDagsterDltResource) -> Any:  # type: ignore[no-untyped-def]
        yield from dlt.run(context=context)

    return materialize([_assets], resources={"dlt": CustomDagsterDltResource()})


def test_identifiers_are_upper_cased(lake: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Snowflake addresses namespaces, tables and columns in all capitals
    through Horizon's REST catalog (ADR-0019), so dlt has to create them that
    way — `ohdp_ingestion.sql_upper`. This is the one place that contract is
    checked end to end; everything downstream (dbt's generated sources, the
    schema/alias macros) is written to match it."""
    _stub_socrata(monkeypatch, [[{"socrata_id": "a", "socrata_updated_at": "2026-01-01", "v": 1}]])
    assert _load(resource_id="abcd-1234", table_name="demo").success

    catalog = _load_test_catalog(lake)
    assert ("HEALTHDATA_GOV",) in catalog.list_namespaces()
    assert ("HEALTHDATA_GOV", "DEMO") in catalog.list_tables("HEALTHDATA_GOV")

    columns = catalog.load_table(("HEALTHDATA_GOV", "DEMO")).scan().to_arrow().column_names
    assert {"SOCRATA_ID", "SOCRATA_UPDATED_AT", "V"} <= set(columns)


def test_write_disposition_follows_the_cursor() -> None:
    from ohdp_ingestion.socrata import write_disposition

    # A cursor means we fetched only the delta, so raw keeps history.
    assert write_disposition("socrata_updated_at") == "append"
    # No cursor means we re-fetched everything; appending would duplicate it.
    assert write_disposition(None) == "replace"


def test_incremental_load_appends_history(lake: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_socrata(monkeypatch, [[{"socrata_id": "a", "socrata_updated_at": "2026-01-01", "v": 1}]])
    result = _load(resource_id="abcd-1234", table_name="demo")
    assert result.success

    # A later run returning a changed row appends rather than replacing: raw is
    # the full history of what the API handed back.
    _stub_socrata(monkeypatch, [[{"socrata_id": "a", "socrata_updated_at": "2026-02-01", "v": 2}]])
    assert _load(resource_id="abcd-1234", table_name="demo").success

    rows = _rows(lake, "healthdata_gov", "demo")
    assert sorted(rows["v"]) == [1, 2]


def test_whole_number_columns_are_cast_to_int_from_socratas_stringified_values(
    lake: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Socrata's SODA API serializes every field as a JSON string, numbers
    included, so a stub that used a native Python ``int``/``float`` for ``v``
    would mask this entirely — matching the real API means stubbing ``"482"``,
    not ``482``. Without the catalog's declared ``number`` type parsed to a
    native Python value before dlt sees it (``source.py``'s
    ``_parse_number_columns``), this column lands as ``text``.

    Socrata's catalog has no int/float distinction — a ``number`` column is
    just as likely to hold ``"482"`` as ``"482.5"`` — so the raw table should
    reflect what the *data* actually was, not force every ``number`` column to
    float regardless of content.
    """
    _stub_socrata(
        monkeypatch,
        [[{"socrata_id": "a", "socrata_updated_at": "2026-01-01", "v": "482"}]],
    )
    result = _load(
        resource_id="abcd-1234",
        table_name="numeric",
        columns=[ColumnSpec(name="v", type="number")],
    )
    assert result.success

    rows = _rows(lake, "healthdata_gov", "numeric")
    assert rows["v"] == [482]
    assert isinstance(rows["v"][0], int)


def test_fractional_number_columns_are_cast_to_float(
    lake: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ``number`` column carrying a decimal value lands as a float, not an
    int — the type follows the data per row-batch, not a fixed column hint."""
    _stub_socrata(
        monkeypatch,
        [[{"socrata_id": "a", "socrata_updated_at": "2026-01-01", "v": "482.5"}]],
    )
    result = _load(
        resource_id="abcd-1234",
        table_name="fractional",
        columns=[ColumnSpec(name="v", type="number")],
    )
    assert result.success

    rows = _rows(lake, "healthdata_gov", "fractional")
    assert rows["v"] == [482.5]
    assert isinstance(rows["v"][0], float)


def test_whole_numbers_too_big_for_bigint_widen_to_float(
    lake: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A whole-number literal wider than signed 64-bit cannot be an int here:
    Python's is unbounded, but dlt's JSON writer raises "Integer exceeds 64-bit
    range" on extract and no destination's `bigint` could store it anyway. CDC
    `atcp-73re` really does publish `site_wval` values around 2.7e21, so this
    widens to a float rather than failing the whole load.
    """
    _stub_socrata(
        monkeypatch,
        [
            [
                {
                    "socrata_id": "a",
                    "socrata_updated_at": "2026-01-01",
                    "v": "2663522980776885000000",
                }
            ]
        ],
    )
    result = _load(
        resource_id="abcd-1234",
        table_name="huge",
        columns=[ColumnSpec(name="v", type="number")],
    )
    assert result.success

    rows = _rows(lake, "healthdata_gov", "huge")
    assert rows["v"] == [2663522980776885000000.0]
    assert isinstance(rows["v"][0], float)


def test_new_columns_evolve_the_schema(lake: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_socrata(monkeypatch, [[{"socrata_id": "a", "socrata_updated_at": "2026-01-01", "v": 1}]])
    assert _load(resource_id="abcd-1234", table_name="drift").success

    # Socrata datasets grow columns without warning; dlt adds the new column and
    # backfills the earlier rows with nulls.
    _stub_socrata(
        monkeypatch,
        [[{"socrata_id": "b", "socrata_updated_at": "2026-02-01", "v": 2, "added": "new"}]],
    )
    assert _load(resource_id="abcd-1234", table_name="drift").success

    rows = _rows(lake, "healthdata_gov", "drift")
    assert "added" in rows
    assert sorted(zip(rows["v"], rows["added"], strict=True)) == [(1, None), (2, "new")]


def test_quiet_run_leaves_the_table_alone(lake: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A wholesale-refresh dataset that returns nothing must not be emptied.

    `replace` on a zero-row extract would wipe the raw history, and "upstream
    published no changes" is not "the dataset is now empty" — the load step
    must be skipped, not just given an empty table. Confirmed empirically that
    a plain ``dlt_pipeline.run()`` does *not* do this on its own; that's what
    ``CustomDagsterDltResource._run`` exists to fix.
    """
    _stub_socrata(monkeypatch, [[{"socrata_id": "a", "v": 1}]])
    assert _load(resource_id="abcd-1234", table_name="full", incremental_cursor=None).success

    _stub_socrata(monkeypatch, [])
    quiet = _load(resource_id="abcd-1234", table_name="full", incremental_cursor=None)
    assert quiet.success
    assert quiet.asset_materializations_for_node("full") == []

    rows = _rows(lake, "healthdata_gov", "full")
    assert rows["v"] == [1]


def test_nested_json_reports_a_materialization_per_child_table(
    lake: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """dlt normalizes a nested array into its own `<table>__<field>` table.
    `SocrataDataset._table_asset()` (components/socrata.py) reports a runless
    materialization for each one, not just the parent — reading the table names
    back off dlt's own event metadata, since re-querying the dlt pipeline's
    schema *after* the run comes back empty (confirmed empirically).
    """
    from ohdp_orchestration.defs.healthdata_gov.component import HealthDataGovDataset

    _stub_socrata(
        monkeypatch,
        [[{"socrata_id": "a", "socrata_updated_at": "2026-01-01", "v": 1, "tags": ["x", "y"]}]],
    )
    ds = HealthDataGovDataset(
        id="abcd-1234",
        name="Nested",
        raw_table="nested",
        cadence="daily",
        enabled=True,
        incremental_cursor="socrata_updated_at",
    )
    instance = DagsterInstance.ephemeral()
    result = materialize(
        [ds._table_asset()], resources={"dlt": CustomDagsterDltResource()}, instance=instance
    )
    assert result.success

    for table in ("nested", "nested__tags"):
        key = AssetKey(["lakehouse", "raw", "healthdata_gov", table])
        assert instance.get_latest_materialization_event(key) is not None, table
