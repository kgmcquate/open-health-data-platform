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
from copy import deepcopy
from typing import Any

import pytest
from dlt.extract.exceptions import ResourceExtractionError

from ohdp_ingestion.openaq.source import openaq_monthly_measurements_source


class _Resp:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        # A deep copy, because the source injects `country`/`sensor_id`/... into
        # the row dicts in place. Real HTTP hands back freshly parsed JSON each
        # call; a stub that returns its canned lists by reference would instead
        # let one call's injected columns overwrite an earlier call's rows, and
        # the routing tables below are deliberately reused across calls.
        return deepcopy(self._payload)


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


# --- fan-out caps ---------------------------------------------------------
# These are the knobs that bound wall-clock time, so what matters is that the
# *calls* stop, not just that the rows do -- `row_limit` above already covers
# rows, and it cannot bound calls (see the source's docstring). Each test
# asserts against `calls`, the recorded URL log, for that reason.


def _many_locations(count: int) -> dict[str, Any]:
    """One matching pm25 sensor and one row per location, ids 1..count."""
    routes: dict[str, Any] = {
        "/locations": [{"id": i, "name": f"Loc {i}"} for i in range(1, count + 1)]
    }
    for i in range(1, count + 1):
        routes[f"/locations/{i}/sensors"] = [{"id": 100 + i, "parameter": {"name": "pm25"}}]
        routes[f"/sensors/{100 + i}/days/monthly"] = [
            {"value": 1.0, "parameter": {"name": "pm25"}, "period": {"label": "2026-01"}}
        ]
    return routes


def test_max_locations_stops_the_walk_not_just_the_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _stub(monkeypatch, _many_locations(10))

    rows = _rows(monkeypatch, countries=["US"], parameters=["pm25"], max_locations=3)

    assert {r["location_id"] for r in rows} == {1, 2, 3}
    # Locations 4..10 were never asked for their sensors at all.
    assert not [c for c in calls if "/locations/4/sensors" in c]
    assert len([c for c in calls if c.endswith("/sensors")]) == 3


def test_max_sensors_caps_the_per_sensor_monthly_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _stub(monkeypatch, _many_locations(10))

    rows = _rows(monkeypatch, countries=["US"], parameters=["pm25"], max_sensors=2)

    assert len(rows) == 2
    assert len([c for c in calls if "days/monthly" in c]) == 2


def test_max_sensors_counts_only_sensors_past_the_parameter_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A filtered-out sensor costs no call, so it must not consume budget."""
    _stub(
        monkeypatch,
        {
            "/locations": [{"id": 1, "name": "Loc A"}],
            "/locations/1/sensors": [
                {"id": 11, "parameter": {"name": "windspeed"}},
                {"id": 12, "parameter": {"name": "windspeed"}},
                {"id": 13, "parameter": {"name": "pm25"}},
            ],
            "/sensors/13/days/monthly": [{"value": 5.0, "parameter": {"name": "pm25"}}],
        },
    )

    rows = _rows(monkeypatch, countries=["US"], parameters=["pm25"], max_sensors=1)

    assert [r["sensor_id"] for r in rows] == [13]


def test_caps_are_per_country(monkeypatch: pytest.MonkeyPatch) -> None:
    """One country's cap must not starve the next of coverage entirely."""
    _stub(monkeypatch, _many_locations(5))

    rows = _rows(monkeypatch, countries=["US", "CA"], parameters=["pm25"], max_locations=2)

    assert {r["country"] for r in rows} == {"US", "CA"}
    assert len(rows) == 4


# --- one bad sensor must not discard the run ------------------------------


def _stub_with_failures(
    monkeypatch: pytest.MonkeyPatch, routes: dict[str, Any], failing: set[int]
) -> None:
    """Like ``_stub``, but ``/sensors/<id>/days/monthly`` raises for ``failing``
    — standing in for a sensor OpenAQ 500s on persistently, i.e. one that has
    already exhausted dlt's own 5xx retries by the time the source sees it."""
    from dlt.sources.helpers import requests

    def _get(url: str, params: dict[str, Any], headers: dict[str, str]) -> _Resp:
        for sensor_id in failing:
            if f"/sensors/{sensor_id}/days/monthly" in url:
                raise RuntimeError("500 Server Error")
        for suffix, results in routes.items():
            if url.endswith(suffix):
                return _Resp({"results": results, "meta": {"found": len(results)}})
        return _Resp({"results": [], "meta": {"found": 0}})

    monkeypatch.setattr(requests, "get", _get)
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)


def test_persistently_failing_sensor_is_skipped_not_fatal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_with_failures(monkeypatch, _many_locations(4), failing={102})

    rows = _rows(monkeypatch, countries=["US"], parameters=["pm25"])

    # Sensor 102 dropped out; every sensor after it still landed, rather than
    # the whole run's work being thrown away at location 2.
    assert {r["sensor_id"] for r in rows} == {101, 103, 104}


def test_long_failure_streak_still_aborts(monkeypatch: pytest.MonkeyPatch) -> None:
    """A streak that long is OpenAQ being down, not bad sensors — it must not
    be swallowed into a quietly empty load."""
    _stub_with_failures(monkeypatch, _many_locations(6), failing=set(range(101, 107)))

    # dlt wraps whatever the resource generator raises — what matters is that
    # the extraction fails rather than quietly landing a near-empty load.
    with pytest.raises(ResourceExtractionError, match="consecutive"):
        _rows(monkeypatch, countries=["US"], parameters=["pm25"], max_consecutive_errors=3)


def test_failure_streak_resets_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scattered bad sensors are not an outage, however many there are in
    total — only an unbroken run of them is."""
    _stub_with_failures(monkeypatch, _many_locations(6), failing={102, 104, 106})

    rows = _rows(monkeypatch, countries=["US"], parameters=["pm25"], max_consecutive_errors=2)

    assert {r["sensor_id"] for r in rows} == {101, 103, 105}
