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

**The 25,000-row `skip` ceiling.** openFDA's own docs: paging with ``skip``
past 25,000 total records isn't supported at all (the API errors) — the
documented way past it is sorting by a unique field and switching to
``search_after`` cursoring. This source doesn't implement that, so every
instance's ``row_limit`` is capped at :data:`ohdp_ingestion.openfda.config.MAX_SKIP`
regardless of what's configured — a hard ceiling, not just a safety cap like
CMS's/OpenAQ's ``row_limit``. A dataset instance whose query matches more rows
than that only ever sees the first ``MAX_SKIP`` of them; narrow further with
``search`` (e.g. a date range or a `country`/`classification` filter) if a
particular slice matters more than an arbitrary prefix of the whole set.

**Always a full replace, never incremental.** An enforcement report's
``status`` (e.g. "Ongoing" -> "Terminated") and other fields can change after
publication, and openFDA gives no reliable ``updated_at`` to page against —
same reasoning CMS/OpenAQ apply for the same conclusion.

**The API key is optional and goes in the query string, not a header.**
Unauthenticated calls work but are throttled harder (40 req/min, 1,000/day vs.
240 req/min, 120,000/day with a key) — unlike OpenAQ, which 401s outright
without one.
"""

from __future__ import annotations

import os
from typing import Any

from dlt.sources.helpers.rest_client.paginators import OffsetPaginator
from dlt.sources.rest_api import RESTAPIConfig, rest_api_source

from ohdp_ingestion.openfda.config import MAX_SKIP

OPENFDA_API_BASE_URL = "https://api.fda.gov/"
_MAX_PAGE_SIZE = 1000  # openFDA's documented ceiling on `limit`


def openfda_source(
    endpoint: str,
    table_name: str,
    *,
    api_key: str | None = None,
    search: str | None = None,
    row_limit: int | None = None,
    page_size: int = _MAX_PAGE_SIZE,
) -> Any:
    """A single-resource ``dlt`` source over one openFDA endpoint, e.g.
    ``endpoint="drug/enforcement"`` for ``GET /drug/enforcement.json``.

    ``api_key`` falls back to ``OHDP_OPENFDA_API_KEY`` (wired end to end like
    ``OHDP_OPENAQ_API_KEY`` — see ``platform/helm/README.md`` and
    ``.github/workflows/deploy-platform.yml``), the same
    read-off-the-environment convention every other source's app-key/token
    uses. Unlike those, it's genuinely optional — an empty key just means
    every request goes out unauthenticated.

    ``rest_api_source`` is given a fixed ``name="openfda"`` — like
    ``cms_source``'s ``name="cms"`` — the dlt *schema* name recorded in the
    destination's pipeline state, which must stay stable across every openFDA
    endpoint instance.
    """
    key = api_key or os.environ.get("OHDP_OPENFDA_API_KEY", "")
    page = min(page_size, _MAX_PAGE_SIZE)
    # See the module docstring: MAX_SKIP is a hard API ceiling, not just a
    # cost control, so it wins even over a larger configured row_limit.
    effective_limit = min(row_limit, MAX_SKIP) if row_limit else MAX_SKIP
    paginator = OffsetPaginator(
        limit=page,
        offset_param="skip",
        limit_param="limit",
        total_path="meta.results.total",
        maximum_offset=effective_limit,
    )

    params: dict[str, Any] = {}
    if search:
        params["search"] = search
    if key:
        params["api_key"] = key

    config: RESTAPIConfig = {
        "client": {"base_url": OPENFDA_API_BASE_URL},
        "resources": [
            {
                "name": table_name,
                "endpoint": {
                    "path": f"{endpoint}.json",
                    "data_selector": "results",
                    "paginator": paginator,
                    "params": params,
                },
                "write_disposition": "replace",
                # What makes the filesystem destination commit an Iceberg
                # table (registered in Horizon) rather than bare Parquet
                # files — see ohdp_ingestion.iceberg_destination.
                "table_format": "iceberg",
            }
        ],
    }
    return rest_api_source(config, name="openfda")
