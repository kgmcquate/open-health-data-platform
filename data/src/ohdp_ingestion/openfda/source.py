# mypy: disable-error-code="no-untyped-def,untyped-decorator,call-overload,no-any-return,arg-type"
"""A ``dlt`` source over openFDA's endpoints, landing an Iceberg table in
``RAW.OPENFDA`` via the shared destination in
:mod:`ohdp_ingestion.iceberg_destination`.

Every ``api.fda.gov`` endpoint (``drug/enforcement``, ``food/enforcement``,
``device/event``, ...) shares one response envelope
(``{"meta": {"results": {"skip", "limit", "total"}}, "results": [...]}``) and
one query-param contract (``search``, ``sort``, ``limit``, ``skip``) — like
CMS's ``data-api/v1`` (:mod:`ohdp_ingestion.cms.source`), that's exactly what
dlt's built-in ``rest_api_source`` is for. Nothing here is bespoke except
which path an instance points at and, unlike CMS, the envelope needs
``data_selector="results"`` (like OpenAQ — :mod:`ohdp_ingestion.openaq.source`
— pulling out of ``{"meta": ..., "results": [...]}`` too).

**Past the 25,000-row `skip` ceiling.** openFDA's own docs: paging with
``skip`` past 25,000 total records isn't supported (the API errors). Every
instance sorts by its cursor field and pages with dlt's
:class:`~dlt.sources.helpers.rest_client.paginators.HeaderLinkPaginator`,
which follows the ``Link: rel="next"`` response header openFDA returns —
that's openFDA's documented ``search_after`` mechanism, so there's no
row-count ceiling on a single run any more (``row_limit`` is now purely an
optional cost/runtime safety valve via ``DltResource.add_limit``, not a
workaround for the API's `skip` cap).

**Merge-on-cursor when a dataset opts in, full replace otherwise.** An
enforcement report's ``status`` (e.g. "Ongoing" -> "Terminated") and other
fields can change after publication, and openFDA gives no push/webhook
notice of that — so a naive "only fetch rows newer than the last cursor"
incremental would silently stop re-syncing older rows' field changes. When a
``DatasetConfig`` sets ``incremental_cursor`` (a date field, e.g.
``report_date``) and ``primary_key`` (a unique field, e.g. ``recall_number``),
this source uses dlt's declarative incremental loading with ``write_disposition
="merge"``: every run advances the true high-water mark forward, but the
*query* sent to openFDA is built from a `lag`-shifted start value
(``incremental_lag_days``, default 90) — so each run also re-fetches and
re-upserts a trailing window of already-ingested rows, catching status
mutations near the cursor without ever re-pulling full history. Rows older
than that window won't have status changes re-synced; that's the same
full-vs-partial-freshness tradeoff CMS/OpenAQ accept elsewhere in this repo,
just narrowed to a window instead of applied to the whole table. A dataset
instance that leaves both fields unset gets the old ``write_disposition
="replace"`` behavior (no incremental block, no merge) — the right fallback
for an endpoint with no reliable date/id field to page and upsert on.

**The API key is optional and goes in the query string, not a header.**
Unauthenticated calls work but are throttled harder (40 req/min, 1,000/day vs.
240 req/min, 120,000/day with a key) — unlike OpenAQ, which 401s outright
without one.
"""

from __future__ import annotations

import os
from typing import Any

from dlt.sources.helpers.rest_client.paginators import HeaderLinkPaginator
from dlt.sources.rest_api import RESTAPIConfig, rest_api_source

OPENFDA_API_BASE_URL = "https://api.fda.gov/"
_MAX_PAGE_SIZE = 1000  # openFDA's documented ceiling on `limit`

# Deliberately far enough back that any openFDA endpoint's full history sorts
# after it, so an unset dlt pipeline state (first run) backfills everything.
_INCREMENTAL_EPOCH = "19000101"


def _build_resource_config(
    endpoint: str,
    table_name: str,
    *,
    api_key: str,
    search: str | None,
    page: int,
    incremental_cursor: str | None,
    primary_key: str | None,
    incremental_lag_days: int,
) -> dict[str, Any]:
    """Pure config-building step, factored out of :func:`openfda_source` so it
    can be unit-tested without going through dlt's ``rest_api_source`` (see
    ``tests/test_openfda_components.py``)."""
    params: dict[str, Any] = {"limit": page}
    if api_key:
        params["api_key"] = api_key

    resource: dict[str, Any] = {
        "name": table_name,
        "endpoint": {
            "path": f"{endpoint}.json",
            "data_selector": "results",
            "paginator": HeaderLinkPaginator(),
            "params": params,
        },
        # What makes the filesystem destination commit an Iceberg table
        # (registered in Horizon) rather than bare Parquet files — see
        # ohdp_ingestion.iceberg_destination.
        "table_format": "iceberg",
    }

    if incremental_cursor:
        # search_after requires an explicit sort on the field you're paging
        # by (openFDA's documented workaround for the skip ceiling above).
        params["sort"] = f"{incremental_cursor}:asc"
        date_clause = f"{incremental_cursor}:[{{incremental.start_value}} TO *]"
        params["search"] = f"{search} AND {date_clause}" if search else date_clause
        resource["write_disposition"] = "merge"
        resource["primary_key"] = primary_key
        resource["endpoint"]["incremental"] = {
            "cursor_path": incremental_cursor,
            "initial_value": _INCREMENTAL_EPOCH,
            "lag": incremental_lag_days,
        }
    else:
        if search:
            params["search"] = search
        resource["write_disposition"] = "replace"

    return resource


def openfda_source(
    endpoint: str,
    table_name: str,
    *,
    api_key: str | None = None,
    search: str | None = None,
    row_limit: int | None = None,
    page_size: int = _MAX_PAGE_SIZE,
    incremental_cursor: str | None = None,
    primary_key: str | None = None,
    incremental_lag_days: int = 90,
) -> Any:
    """A single-resource ``dlt`` source over one openFDA endpoint, e.g.
    ``endpoint="drug/enforcement"`` for ``GET /drug/enforcement.json``.

    ``api_key`` falls back to ``OHDP_OPENFDA_API_KEY`` (wired end to end like
    ``OHDP_OPENAQ_API_KEY`` — see ``platform/helm/README.md`` and
    ``.github/workflows/deploy-platform.yml``), the same
    read-off-the-environment convention every other source's app-key/token
    uses. Unlike those, it's genuinely optional — an empty key just means
    every request goes out unauthenticated.

    ``incremental_cursor``/``primary_key`` (see the module docstring) switch
    this resource from full ``replace`` to `lag`-windowed ``merge``; leave
    both ``None`` to keep the old full-replace behavior.

    ``rest_api_source`` is given a fixed ``name="openfda"`` — like
    ``cms_source``'s ``name="cms"`` — the dlt *schema* name recorded in the
    destination's pipeline state, which must stay stable across every openFDA
    endpoint instance.
    """
    resource = _build_resource_config(
        endpoint,
        table_name,
        api_key=api_key or os.environ.get("OHDP_OPENFDA_API_KEY", ""),
        search=search,
        page=min(page_size, _MAX_PAGE_SIZE),
        incremental_cursor=incremental_cursor,
        primary_key=primary_key,
        incremental_lag_days=incremental_lag_days,
    )
    config: RESTAPIConfig = {
        "client": {"base_url": OPENFDA_API_BASE_URL},
        "resources": [resource],
    }
    source = rest_api_source(config, name="openfda")
    if row_limit:
        source.resources[table_name].add_limit(row_limit, count_rows=True)
    return source
