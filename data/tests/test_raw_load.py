"""The raw loader writes straight to Snowflake in prod (ADR-0014). Socrata is
stubbed; the destination is stubbed too, to a local SQLite file via dlt's
generic ``sqlalchemy`` destination — a real SQL engine (schema evolution,
``replace``'s truncate, nested-array flattening all behave the same as a real
warehouse) with no external service and no DuckDB dependency.

Exercises ``CustomDagsterDltResource`` (the actual production code path —
``ohdp_orchestration.defs.healthdata_gov.component`` runs every table asset
through it) rather than a parallel test-only reimplementation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import dlt
import pytest
from dagster import AssetKey, DagsterInstance, materialize
from dagster_dlt import dlt_assets

from ohdp_ingestion.healthdata_gov.source import build_pipeline, socrata_source
from ohdp_orchestration.resources.dlt import CustomDagsterDltResource


@pytest.fixture
def lake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Stub the raw loader's Snowflake destination with a local SQLite file,
    and point dlt's own state dir at tmp_path too."""
    db_path = tmp_path / "raw.db"
    # dlt keeps pipeline state (the incremental cursor) here.
    monkeypatch.setenv("DLT_DATA_DIR", str(tmp_path / "dlt"))

    import ohdp_ingestion.healthdata_gov.source as src

    monkeypatch.setattr(
        src,
        "_destination",
        lambda: dlt.destinations.sqlalchemy(credentials=f"sqlite:///{db_path}"),
    )
    return db_path


def _rows(db_path: Path, schema: str, table: str) -> dict[str, Any]:
    # A fresh, throwaway pipeline pointed at the same file/dataset reads back
    # whatever any other pipeline loaded there — dlt's dataset API queries the
    # live destination schema, not this pipeline's own local state.
    reader = dlt.pipeline(
        pipeline_name="test_reader",
        destination=dlt.destinations.sqlalchemy(credentials=f"sqlite:///{db_path}"),
        dataset_name=schema,
    )
    arrow_table = reader.dataset()[table].arrow()
    assert arrow_table is not None
    return dict(arrow_table.to_pydict())


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
):
    """Materialize one table asset the same way the component does, through
    ``CustomDagsterDltResource``."""

    @dlt_assets(
        dlt_source=socrata_source(resource_id, table_name, incremental_cursor=incremental_cursor),
        dlt_pipeline=build_pipeline(pipeline_name=f"{source}_{table_name}", source=source),
        name=table_name,
    )
    def _assets(context, dlt: CustomDagsterDltResource):
        yield from dlt.run(context=context)

    return materialize([_assets], resources={"dlt": CustomDagsterDltResource()})


def test_write_disposition_follows_the_cursor() -> None:
    from ohdp_ingestion.healthdata_gov.source import write_disposition

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
    `HealthDataGovDataset._table_asset()` (healthdata_gov/component.py) reports
    a runless materialization for each one, not just the parent — reading the
    table names back off dlt's own event metadata, since re-querying the dlt
    pipeline's schema *after* the run comes back empty (confirmed empirically).
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
        key = AssetKey(["warehouse", "RAW", "healthdata_gov", table])
        assert instance.get_latest_materialization_event(key) is not None, table
