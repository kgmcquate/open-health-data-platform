"""Offline checks for the hand-rolled OpenAQ monthly-measurements fan-out
(``ohdp_ingestion.openaq.source.openaq_monthly_measurements_source``).

This is the riskiest new code in the OpenAQ ingestion: it makes one HTTP call
per matched sensor with no bulk/batch alternative, so a bug here is expensive
to discover live (burned free-tier quota, or worse, a silently wrong dataset).
Stubs ``dlt.sources.helpers.requests.get`` the same way ``test_raw_load.py``
stubs Socrata, routing on URL rather than call order since this source makes
three different kinds of call (locations, per-location sensors, per-sensor
monthly rollup). The rate limiter's sleep is patched out — its own timing
logic is simple enough to trust without burning real wall-clock time here.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from ohdp_ingestion.openaq.source import openaq_monthly_measurements_source


class _Resp:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


def _stub(monkeypatch: pytest.MonkeyPatch, routes: dict[str, list[dict[str, Any]]]) -> list[str]:
    """``routes`` maps a URL suffix (the part after the base URL) to the
    ``results`` list it should return — an empty list stops that path's
    pagination loop, matching every other source's stop condition here."""
    from dlt.sources.helpers import requests

    calls: list[str] = []

    def _get(url: str, params: dict[str, Any], headers: dict[str, str]) -> _Resp:
        calls.append(url)
        for suffix, results in routes.items():
            if url.endswith(suffix):
                return _Resp({"results": results, "meta": {"found": len(results)}})
        return _Resp({"results": [], "meta": {"found": 0}})

    monkeypatch.setattr(requests, "get", _get)
    # `time` is a shared module object, so patching it here also affects the
    # `time.sleep` call inside ohdp_ingestion.openaq.source's rate limiter.
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    return calls


def _rows(monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> list[dict[str, Any]]:
    """dlt flattens a resource that yields batches (``list[dict]``) into
    individual row dicts when the resource itself is iterated directly, so
    this reads rows straight off it rather than off the batches it yields."""
    src = openaq_monthly_measurements_source("monthly_measurements", api_key="test-key", **kwargs)
    return [row for resource in src.resources.values() for row in resource]


def test_filters_sensors_by_parameter_and_injects_identifying_columns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub(
        monkeypatch,
        {
            "/locations": [
                {"id": 1, "name": "Loc A"},
                {"id": 2, "name": "Loc B"},
            ],
            "/locations/1/sensors": [
                {"id": 11, "parameter": {"name": "pm25"}},
                {"id": 12, "parameter": {"name": "windspeed"}},  # not a criteria pollutant
            ],
            "/locations/2/sensors": [
                {"id": 21, "parameter": {"name": "o3"}},
            ],
            "/sensors/11/days/monthly": [
                {"value": 8.2, "parameter": {"name": "pm25"}, "period": {"label": "2026-01"}},
            ],
            "/sensors/21/days/monthly": [
                {"value": 0.03, "parameter": {"name": "o3"}, "period": {"label": "2026-01"}},
            ],
        },
    )

    rows = _rows(monkeypatch, countries=["US"], parameters=["pm25", "o3"])

    assert len(rows) == 2
    by_sensor = {r["sensor_id"]: r for r in rows}
    assert by_sensor[11]["location_id"] == 1
    assert by_sensor[11]["location_name"] == "Loc A"
    assert by_sensor[11]["country"] == "US"
    assert by_sensor[11]["value"] == 8.2
    assert by_sensor[21]["location_id"] == 2
    # windspeed sensor 12 never reached the monthly endpoint at all.
    assert 12 not in by_sensor


def test_no_parameter_filter_keeps_every_sensor(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(
        monkeypatch,
        {
            "/locations": [{"id": 1, "name": "Loc A"}],
            "/locations/1/sensors": [
                {"id": 11, "parameter": {"name": "pm25"}},
                {"id": 12, "parameter": {"name": "windspeed"}},
            ],
            "/sensors/11/days/monthly": [{"value": 8.2, "parameter": {"name": "pm25"}}],
            "/sensors/12/days/monthly": [{"value": 3.1, "parameter": {"name": "windspeed"}}],
        },
    )

    rows = _rows(monkeypatch, countries=["US"], parameters=[])

    assert {r["sensor_id"] for r in rows} == {11, 12}


def test_monitor_filter_is_sent_when_reference_monitors_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _stub(monkeypatch, {"/locations": []})

    list(_rows(monkeypatch, countries=["US"], parameters=["pm25"], reference_monitors_only=True))

    assert any("/locations" in c for c in calls)


def test_row_limit_truncates_across_sensors(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(
        monkeypatch,
        {
            "/locations": [{"id": 1, "name": "Loc A"}],
            "/locations/1/sensors": [
                {"id": 11, "parameter": {"name": "pm25"}},
                {"id": 12, "parameter": {"name": "o3"}},
            ],
            "/sensors/11/days/monthly": [
                {"value": 1, "parameter": {"name": "pm25"}},
                {"value": 2, "parameter": {"name": "pm25"}},
            ],
            "/sensors/12/days/monthly": [
                {"value": 3, "parameter": {"name": "o3"}},
            ],
        },
    )

    rows = _rows(monkeypatch, countries=["US"], parameters=["pm25", "o3"], row_limit=1)

    assert len(rows) == 1
