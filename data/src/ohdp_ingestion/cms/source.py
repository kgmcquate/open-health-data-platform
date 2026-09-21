# mypy: disable-error-code="no-untyped-def,untyped-decorator,call-overload,no-any-return,arg-type"
"""A ``dlt`` source over one CMS dataset, landing an Iceberg table in
``RAW.CMS`` via the shared destination in
:mod:`ohdp_ingestion.iceberg_destination` (ADR-0026).

Unlike :mod:`ohdp_ingestion.socrata.source`, this isn't a hand-rolled
``@dlt.source`` — ``data-api/v1`` is a plain paginated REST API (offset +
page-size query params, a bare JSON array per page, no envelope), which is
exactly what dlt's built-in ``rest_api_source`` is for. Nothing here is
bespoke except pointing it at the right path and paginator; the Iceberg
landing side is the same shared destination Socrata sources use.

**Always a full replace, never incremental.** CMS's API has no per-row
``:id``/``:updated_at`` the way Socrata's does — a distribution is a whole
snapshot republished on its own cadence (``accrualPeriodicity``), so there is
no row-level cursor to fetch against. ``write_disposition="replace"``
unconditionally.
"""

from __future__ import annotations

from typing import Any

from dlt.sources.helpers.rest_client.paginators import OffsetPaginator
from dlt.sources.rest_api import RESTAPIConfig, rest_api_source

CMS_API_BASE_URL = "https://data.cms.gov/data-api/v1/"
_MAX_PAGE = 5_000  # CMS's documented ceiling on `size`


def cms_source(
    dataset_id: str,
    table_name: str,
    *,
    row_limit: int | None = None,
    page_size: int = _MAX_PAGE,
) -> Any:
    """A single-resource ``dlt`` source for CMS dataset ``dataset_id``.

    ``rest_api_source`` is given a fixed ``name="cms"`` — like
    ``socrata_source``'s ``name=socrata.source``, this becomes the dlt *schema*
    name recorded in the destination's pipeline state, and must stay stable
    across every CMS dataset instance.
    """
    page = min(page_size, _MAX_PAGE)
    paginator = OffsetPaginator(
        limit=page,
        offset_param="offset",
        limit_param="size",
        # The `/data` response is a bare JSON array with no envelope, so there
        # is no `total` field to read a stop condition from (the default
        # `total_path="total"` would just 404 the whole run looking for one).
        # `stop_after_empty_page` (default True) already stops correctly once
        # a page comes back empty — the actual end-of-data signal here.
        total_path=None,
        # dlt's OffsetPaginator counts *pages requested*, not rows returned —
        # a page's `current_value` advances by `limit` regardless of how many
        # rows it held. Capping via `maximum_offset` is therefore an
        # upper bound on rows fetched, not an exact cutoff (the last page can
        # overshoot by up to `page - 1` rows), same trade-off Socrata's own
        # row_limit slicing makes.
        maximum_offset=row_limit,
    )

    config: RESTAPIConfig = {
        "client": {"base_url": CMS_API_BASE_URL},
        "resources": [
            {
                "name": table_name,
                "endpoint": {
                    "path": f"dataset/{dataset_id}/data",
                    "paginator": paginator,
                },
                "write_disposition": "replace",
                # What makes the filesystem destination commit an Iceberg
                # table (registered in Horizon) rather than bare Parquet
                # files — see ohdp_ingestion.iceberg_destination.
                "table_format": "iceberg",
            }
        ],
    }
    return rest_api_source(config, name="cms")
