# mypy: disable-error-code="no-untyped-def,untyped-decorator,call-overload,no-any-return,arg-type"
"""``dlt`` sources over two OpenAQ v3 resources, both landing Iceberg tables in
``RAW.OPENAQ`` via the shared destination in
:mod:`ohdp_ingestion.iceberg_destination`.

``openaq_locations_source`` — plain paginated REST, dlt's built-in
``rest_api_source`` (like CMS's ``data-api/v1``,
:mod:`ohdp_ingestion.cms.source`). ``openaq_monthly_measurements_source`` is
hand-rolled instead (like :mod:`ohdp_ingestion.socrata.source`): getting a
month's average out of OpenAQ means fanning out *locations -> sensors ->
that sensor's monthly rollup*, one HTTP call per sensor with no bulk/batch
query across sensors — not something ``rest_api_source``'s declarative
parent/child resolution buys much over just writing the loop, and the fan-out
is exactly where the API-call-cost control (``reference_monitors_only``,
``parameters``) has to live. See that function's docstring for the budget
math.

Every ``/v3`` endpoint requires the ``X-API-Key`` header both sources send.

**Locations is always a full replace, never incremental** — neither dlt nor
OpenAQ gives ``/v3/locations`` a per-row ``updated_at`` to page against, and
its ``order_by`` only accepts ``id`` (checked against the live spec at
``https://api.openaq.org/openapi.json``), so ``write_disposition="replace"``
unconditionally, same reasoning as CMS.

**Monthly measurements is incremental with a bounded lookback**, not a true
delta feed: ``/v3/sensors/{id}/days/monthly`` does take ``date_from``/
``date_to`` (unlike ``/locations``), but that bounds *which months come
back*, not *what changed* — a sensor's monthly rollup keeps being recomputed
as more of that month's raw data arrives, so there's still no cursor that
could safely mean "only fetch what's new" without silently missing a
revision to a month already scanned past. ``openaq_monthly_measurements_source``
instead re-fetches a trailing window (``lookback_months``) every run and
lands it with ``write_disposition="append"`` — raw keeps every version of a
revised month rather than overwriting it in place (ADR-0010), and the clean
layer (``openaq_current_rows``) dedupes to the latest ``_dlt_load_id`` per
``(sensor_id, period_label)``, the same pattern Socrata's incremental
sources use (``socrata_current_rows``).
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import Any

import dlt
from dlt.sources.helpers import requests
from dlt.sources.helpers.rest_client.paginators import PageNumberPaginator
from dlt.sources.rest_api import RESTAPIConfig, rest_api_source

OPENAQ_API_BASE_URL = "https://api.openaq.org/v3/"
_MAX_PAGE_SIZE = 1000  # OpenAQ's documented ceiling on `limit`


def openaq_locations_source(
    table_name: str,
    *,
    api_key: str | None = None,
    row_limit: int | None = None,
    page_size: int = _MAX_PAGE_SIZE,
) -> Any:
    """A single-resource ``dlt`` source over OpenAQ's ``/v3/locations``.

    ``api_key`` falls back to ``OHDP_OPENAQ_API_KEY`` (already wired end to
    end into every pipeline pod — see ``platform/helm/README.md`` and
    ``.github/workflows/deploy-platform.yml``), the same
    read-off-the-environment convention ``SocrataDomain.app_token_env`` uses
    for its app token.

    ``rest_api_source`` is given a fixed ``name="openaq"`` — like
    ``cms_source``'s ``name="cms"``, this becomes the dlt *schema* name
    recorded in the destination's pipeline state, and must stay stable across
    every OpenAQ resource instance.
    """
    key = api_key or os.environ.get("OHDP_OPENAQ_API_KEY", "")
    page = min(page_size, _MAX_PAGE_SIZE)
    paginator = PageNumberPaginator(
        base_page=1,
        page_param="page",
        # OpenAQ's response envelope: {"meta": {"found": N, ...}, "results": [...]}.
        total_path="meta.found",
        # PageNumberPaginator counts *pages requested*, like CMS's
        # OffsetPaginator counts offsets — an upper bound on rows fetched, not
        # an exact cutoff, same trade-off ohdp_ingestion.cms.source makes.
        maximum_page=(row_limit // page + 1) if row_limit else None,
    )

    config: RESTAPIConfig = {
        "client": {
            "base_url": OPENAQ_API_BASE_URL,
            "headers": {"X-API-Key": key} if key else {},
        },
        "resources": [
            {
                "name": table_name,
                "endpoint": {
                    "path": "locations",
                    "data_selector": "results",
                    "paginator": paginator,
                    "params": {"limit": page},
                },
                "write_disposition": "replace",
                # What makes the filesystem destination commit an Iceberg
                # table (registered in Horizon) rather than bare Parquet
                # files — see ohdp_ingestion.iceberg_destination.
                "table_format": "iceberg",
            }
        ],
    }
    return rest_api_source(config, name="openaq")


# OpenAQ's documented free-tier cap is 60/min *and* 2,000/hour — the hourly
# figure is the binding one (60/min sustained is 3,600/hour, well over it), so
# the sustained rate has to target the hourly cap, not the per-minute one.
# 25/min = 1,500/hour leaves headroom for the occasional retry without
# tipping into 429s. A fixed inter-request sleep, not a sliding-window
# counter: simplest thing that can't burst, and every call in this source
# (locations list, per-location sensors, per-sensor monthly rollup) shares
# one budget, so throttling has to sit below all three, not per-endpoint.
_MAX_REQUESTS_PER_MINUTE = 25
_MIN_REQUEST_INTERVAL = 60.0 / _MAX_REQUESTS_PER_MINUTE


class _RateLimiter:
    def __init__(self, min_interval: float = _MIN_REQUEST_INTERVAL) -> None:
        self._min_interval = min_interval
        self._last_call: float | None = None

    def wait(self) -> None:
        now = time.monotonic()
        if self._last_call is not None:
            elapsed = now - self._last_call
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
        self._last_call = time.monotonic()


def _get(
    url: str, *, params: dict[str, Any], headers: dict[str, str], limiter: _RateLimiter
) -> Any:
    limiter.wait()
    resp = requests.get(url, params=params, headers=headers)
    resp.raise_for_status()
    return resp.json()


def _months_ago(months: int) -> str:
    """The first-of-month ISO date ``months`` back from today (UTC).

    Used as ``date_from`` for ``/sensors/{id}/days/monthly`` — a trailing
    window, not a persisted cursor, because there's no "since <date>" that
    would be safe against a month getting revised after it's first fetched
    (see ``openaq_monthly_measurements_source``'s docstring).
    """
    today = datetime.now(UTC).date()
    year, month = today.year, today.month - months
    while month <= 0:
        month += 12
        year -= 1
    return date(year, month, 1).isoformat()


def openaq_monthly_measurements_source(
    table_name: str,
    *,
    api_key: str | None = None,
    countries: list[str] | None = None,
    parameters: list[str] | None = None,
    reference_monitors_only: bool = True,
    row_limit: int | None = None,
    lookback_months: int | None = 3,
) -> Any:
    """A hand-rolled ``dlt`` source: monthly-average air-quality readings for
    every sensor at every location matching ``countries``/``parameters``.

    **Why this needs three levels of API calls, not one.** OpenAQ has no
    endpoint that returns "monthly averages for country X" directly —
    ``/v3/sensors/{id}/days/monthly`` (the rollup OpenAQ's own docs mark
    "preferred" over the coarser ``/measurements/monthly``) takes a *sensor*
    id, and sensors are only discoverable per-location
    (``/v3/locations/{id}/sensors`` — there is no flat ``/v3/sensors`` list).
    So: page ``/v3/locations`` once for the matching location set, call
    ``/locations/{id}/sensors`` once per location, keep only sensors whose
    ``parameter.name`` is in ``parameters``, then call
    ``/sensors/{id}/days/monthly`` once per surviving sensor. There is no
    batch/bulk variant of that last call, and the *discovery* walk
    (locations -> sensors) has no incremental angle at all — it has to run
    in full every time to find this run's matching sensor set. ``days/monthly``
    itself does take ``date_from``/``date_to`` (see ``lookback_months``
    below), which bounds how much history that last call returns, not
    whether it gets called.

    **Why ``reference_monitors_only`` and a small ``parameters`` list are load
    -bearing, not just scope.** Call count scales with
    ``len(matching locations) + len(matching sensors)``, and the per-sensor
    call is unavoidable regardless of date range — narrowing the date window
    shrinks rows per call, not the number of calls. OpenAQ's US location
    count including every low-cost sensor network runs well into the
    thousands; restricted to ``monitor=true`` (government reference-grade
    stations, OpenAQ's own filter) it's roughly 1,000-1,500 — the difference
    between a call budget that comfortably fits a monthly scheduled run and
    one that takes several hours *every single run forever* (``lookback_months``
    shrinks payload size, not this call count — see the module docstring).

    ``countries`` is a list of ISO 3166-1 alpha-2 codes (``/v3/locations``'
    own ``iso`` filter only takes one at a time, so this fans out one
    locations-list pull per country). ``parameters`` is OpenAQ's own
    parameter *names* (``"pm25"``, not a numeric ``parameters_id`` — avoids
    needing a hardcoded id lookup table that could silently drift from
    OpenAQ's own).

    ``lookback_months`` bounds ``days/monthly`` to the trailing N months
    (``date_from=_months_ago(lookback_months)``, no ``date_to``) and lands
    rows with ``write_disposition="append"`` plus ``primary_key=("sensor_id",
    "period_label")`` — raw accumulates every version of a recently-revised
    month rather than overwriting it, and the clean layer dedupes to the
    latest load (see the module docstring). Pass ``None`` for a full
    historical backfill instead (every month, every surviving sensor,
    landed with ``write_disposition="replace"`` as before) — the right
    choice for a first run against an empty raw table, or to rebuild it from
    scratch.
    """
    key = api_key or os.environ.get("OHDP_OPENAQ_API_KEY", "")
    headers = {"X-API-Key": key} if key else {}
    country_list = countries or ["US"]
    parameter_set = frozenset(parameters or [])
    base = OPENAQ_API_BASE_URL.rstrip("/")
    date_from = _months_ago(lookback_months) if lookback_months is not None else None

    @dlt.source(name="openaq")
    def _source():
        @dlt.resource(
            name=table_name,
            write_disposition="append" if date_from else "replace",
            primary_key=("sensor_id", "period_label") if date_from else None,
            table_format="iceberg",
        )
        def rows() -> Iterator[list[dict[str, Any]]]:
            limiter = _RateLimiter()
            seen = 0
            for country in country_list:
                locations = _iter_locations(
                    base, country, reference_monitors_only, headers, limiter
                )
                for location in locations:
                    location_id = location["id"]
                    sensors = _get(
                        f"{base}/locations/{location_id}/sensors",
                        params={"limit": _MAX_PAGE_SIZE},
                        headers=headers,
                        limiter=limiter,
                    ).get("results", [])
                    for sensor in sensors:
                        parameter_name = (sensor.get("parameter") or {}).get("name")
                        if parameter_set and parameter_name not in parameter_set:
                            continue
                        sensor_id = sensor["id"]
                        batch = []
                        for row in _iter_sensor_monthly(
                            base, sensor_id, headers, limiter, date_from
                        ):
                            row["location_id"] = location_id
                            row["location_name"] = location.get("name")
                            row["country"] = country
                            row["sensor_id"] = sensor_id
                            row["period_label"] = (row.get("period") or {}).get("label")
                            batch.append(row)
                        if row_limit is not None and batch:
                            remaining = row_limit - seen
                            if remaining <= 0:
                                return
                            batch = batch[:remaining]
                        if batch:
                            yield batch
                            seen += len(batch)
                        if row_limit is not None and seen >= row_limit:
                            return

        return rows

    return _source()


def _iter_locations(
    base: str, country: str, monitor_only: bool, headers: dict[str, str], limiter: _RateLimiter
) -> Iterator[dict[str, Any]]:
    page = 1
    while True:
        params: dict[str, Any] = {
            "iso": country,
            "limit": _MAX_PAGE_SIZE,
            "page": page,
            "order_by": "id",
            "sort_order": "asc",
        }
        if monitor_only:
            params["monitor"] = "true"
        batch = _get(f"{base}/locations", params=params, headers=headers, limiter=limiter).get(
            "results", []
        )
        if not batch:
            return
        yield from batch
        if len(batch) < _MAX_PAGE_SIZE:
            return
        page += 1


def _iter_sensor_monthly(
    base: str,
    sensor_id: int,
    headers: dict[str, str],
    limiter: _RateLimiter,
    date_from: str | None = None,
) -> Iterator[dict[str, Any]]:
    page = 1
    while True:
        params: dict[str, Any] = {"limit": _MAX_PAGE_SIZE, "page": page}
        if date_from:
            params["date_from"] = date_from
        batch = _get(
            f"{base}/sensors/{sensor_id}/days/monthly",
            params=params,
            headers=headers,
            limiter=limiter,
        ).get("results", [])
        if not batch:
            return
        yield from batch
        if len(batch) < _MAX_PAGE_SIZE:
            return
        page += 1
