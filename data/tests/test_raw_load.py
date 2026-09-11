"""The raw loader's two-step path: dlt stages the delta, `commit` lands it in
Iceberg (ADR-0011). Socrata is stubbed; the catalog is a local SqlCatalog."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def lake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Point both the catalog and the dlt staging root at tmp_path."""
    monkeypatch.setenv("OHDP_ICEBERG_CATALOG_URI", "")
    monkeypatch.setenv("OHDP_ICEBERG_LOCAL_CATALOG_PATH", str(tmp_path / "cat.db"))
    monkeypatch.setenv("OHDP_ICEBERG_LOCAL_WAREHOUSE", str(tmp_path / "wh"))
    # dlt keeps pipeline state (the incremental cursor) here, not in the lake.
    monkeypatch.setenv("DLT_DATA_DIR", str(tmp_path / "dlt"))

    import ohdp_ingestion.healthdata_gov.source as src
    import ohdp_ingestion.iceberg as ice
    from ohdp_shared.settings import Settings

    settings = Settings()
    monkeypatch.setattr(ice, "settings", settings)
    monkeypatch.setattr(src, "settings", settings)
    return ice


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


def test_strategy_follows_the_cursor() -> None:
    from ohdp_ingestion.healthdata_gov.source import iceberg_strategy

    # A cursor means we fetched only the delta, so raw keeps history.
    assert iceberg_strategy("socrata_updated_at") == "append"
    # No cursor means we re-fetched everything; appending would duplicate it.
    assert iceberg_strategy(None) == "overwrite"


def test_incremental_load_appends_history(lake: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from ohdp_ingestion.healthdata_gov.source import load_raw_table

    _stub_socrata(monkeypatch, [[{"socrata_id": "a", "socrata_updated_at": "2026-01-01", "v": 1}]])
    first = load_raw_table(resource_id="abcd-1234", table_name="demo", source="healthdata_gov")
    assert first.rows == 1
    assert first.strategy == "append"

    # A later run returning a changed row appends rather than replacing: raw is
    # the full history of what the API handed back.
    _stub_socrata(monkeypatch, [[{"socrata_id": "a", "socrata_updated_at": "2026-02-01", "v": 2}]])
    load_raw_table(resource_id="abcd-1234", table_name="demo", source="healthdata_gov")

    rows = lake.load_catalog().load_table("raw_healthdata_gov.demo").scan().to_arrow().to_pydict()
    assert sorted(rows["v"]) == [1, 2]


def test_new_columns_evolve_the_schema(lake: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from ohdp_ingestion.healthdata_gov.source import load_raw_table

    _stub_socrata(monkeypatch, [[{"socrata_id": "a", "socrata_updated_at": "2026-01-01", "v": 1}]])
    load_raw_table(resource_id="abcd-1234", table_name="drift", source="healthdata_gov")

    # Socrata datasets grow columns without warning; the commit unions them in
    # and backfills the earlier rows with nulls.
    _stub_socrata(
        monkeypatch,
        [[{"socrata_id": "b", "socrata_updated_at": "2026-02-01", "v": 2, "added": "new"}]],
    )
    load_raw_table(resource_id="abcd-1234", table_name="drift", source="healthdata_gov")

    rows = lake.load_catalog().load_table("raw_healthdata_gov.drift").scan().to_arrow().to_pydict()
    assert "added" in rows
    assert sorted(zip(rows["v"], rows["added"], strict=True)) == [(1, None), (2, "new")]


def test_quiet_run_leaves_the_table_alone(lake: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """A wholesale-refresh dataset that returns nothing must not be emptied.

    `overwrite` on an empty staging table would wipe the raw history, and
    "upstream published no changes" is not "the dataset is now empty".
    """
    from ohdp_ingestion.healthdata_gov.source import load_raw_table

    _stub_socrata(monkeypatch, [[{"socrata_id": "a", "v": 1}]])
    load_raw_table(
        resource_id="abcd-1234",
        table_name="full",
        source="healthdata_gov",
        incremental_cursor=None,
    )

    _stub_socrata(monkeypatch, [])
    quiet = load_raw_table(
        resource_id="abcd-1234",
        table_name="full",
        source="healthdata_gov",
        incremental_cursor=None,
    )

    assert quiet.rows == 0
    rows = lake.load_catalog().load_table("raw_healthdata_gov.full").scan().to_arrow().to_pydict()
    assert rows["v"] == [1]
