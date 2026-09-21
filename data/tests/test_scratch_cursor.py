"""SCRATCH: does the incremental cursor / column type survive from run to run?"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from test_raw_load import _load, _rows, lake  # noqa: F401

from ohdp_ingestion.socrata import ColumnSpec

_CAPTURED: list[dict[str, Any]] = []


def _capture_socrata(monkeypatch: pytest.MonkeyPatch, pages: list[list[dict[str, Any]]]) -> None:
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
        _CAPTURED.append(dict(params))
        return _Resp(remaining.pop(0) if remaining else [])

    monkeypatch.setattr(requests, "get", _get)


def test_cursor_and_schema_persist(lake: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    cols = [ColumnSpec(name="v", type="number")]
    _capture_socrata(
        monkeypatch,
        [[{"socrata_id": "a", "socrata_updated_at": "2026-01-01", "v": "482.5"}]],
    )
    assert _load(resource_id="abcd-1234", table_name="persist", columns=cols).success
    _CAPTURED.clear()

    _capture_socrata(
        monkeypatch,
        [[{"socrata_id": "b", "socrata_updated_at": "2026-02-01", "v": "482.75"}]],
    )
    result = _load(resource_id="abcd-1234", table_name="persist", columns=cols)
    print(">>> run2 $where params:", [p.get("$where") for p in _CAPTURED])
    print(">>> run2 success:", result.success)
    print(">>> rows:", _rows(lake, "healthdata_gov", "persist"))
