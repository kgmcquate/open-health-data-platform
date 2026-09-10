# dlt's @source/@resource decorators are untyped and dlt.pipeline()'s overloads
# reject a Destination object in the `destination` position; both are the
# documented usage. Relax only this module.
# mypy: disable-error-code="no-untyped-def, untyped-decorator, call-overload, no-any-return"
"""A ``dlt`` source over one Socrata dataset, landing an Iceberg table in `raw`.

Every row carries two system columns we alias in explicitly:

* ``socrata_id``  (``:id``)          — stable per-row key; the ``clean`` layer
  dedupes on it.
* ``socrata_updated_at`` (``:updated_at``) — row mutation time; the incremental
  cursor, so re-runs only *fetch* rows that changed. The raw table itself is
  **append-only** (ADR-0010) — full history of what the API returned — and its
  schema auto-evolves (dlt runs ``union_by_name`` on every append).

``$order=:id`` gives a stable pagination key.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import dlt
from dlt.sources.helpers import requests

from ohdp_ingestion.iceberg import configure_dlt, is_local, namespace
from ohdp_shared import get_logger
from ohdp_shared.settings import settings

log = get_logger(__name__)

_SYSTEM_SELECT = "*,:id AS socrata_id,:updated_at AS socrata_updated_at"
_EPOCH = "1900-01-01T00:00:00.000"
_MAX_PAGE = 50_000  # Socrata hard ceiling on $limit


@dlt.source(name="healthdata_gov")
def socrata_source(
    resource_id: str,
    table_name: str,
    *,
    domain: str = "healthdata.gov",
    incremental_cursor: str | None = "socrata_updated_at",
    row_limit: int | None = None,
    page_size: int = 5_000,
    app_token: str | None = None,
):
    """A single-resource source for ``resource_id``, written append-only.

    ``incremental_cursor`` only controls the SoQL ``$where`` (fetch efficiency);
    set it to ``None`` to re-fetch the whole dataset each run (correct for
    datasets Socrata rewrites wholesale).
    """
    base_url = f"https://{domain}/resource/{resource_id}.json"
    token = app_token or os.environ.get("OHDP_HEALTHDATA_APP_TOKEN") or None
    headers = {"X-App-Token": token} if token else {}
    page = min(page_size, _MAX_PAGE)
    # Append when we can fetch incrementally (raw = full history). For datasets
    # Socrata rewrites wholesale (no usable cursor) we re-fetch everything each
    # run, so `replace` the snapshot instead of appending duplicates — Iceberg
    # keeps the prior snapshots for time travel either way.
    write_disposition = "append" if incremental_cursor else "replace"

    @dlt.resource(
        name=table_name,
        primary_key="socrata_id",
        write_disposition=write_disposition,
        table_format="iceberg",
    )
    # dlt's documented pattern is to default the arg to the incremental object;
    # B008 flags the call in the default but that is exactly how the hint is wired.
    def rows(
        cursor: dlt.sources.incremental[str] | None = (
            dlt.sources.incremental(incremental_cursor, initial_value=_EPOCH)  # noqa: B008
            if incremental_cursor
            else None
        ),
    ) -> Iterator[list[dict[str, Any]]]:
        offset = 0
        seen = 0
        since = cursor.last_value if cursor is not None else None
        while True:
            params: dict[str, Any] = {
                "$select": _SYSTEM_SELECT,
                "$order": ":id",
                "$limit": page,
                "$offset": offset,
            }
            if since and since != _EPOCH:
                params["$where"] = f":updated_at > '{since}'"

            resp = requests.get(base_url, params=params, headers=headers)
            resp.raise_for_status()
            batch = resp.json()
            if not isinstance(batch, list):
                raise TypeError(f"{resource_id}: expected a JSON array, got {type(batch).__name__}")
            if not batch:
                return

            if row_limit is not None and seen + len(batch) > row_limit:
                batch = batch[: row_limit - seen]
            yield batch
            seen += len(batch)

            if len(batch) < page or (row_limit is not None and seen >= row_limit):
                return
            offset += page

    return rows


def _filesystem_destination() -> Any:
    """dlt ``filesystem`` destination rooted at the lake, with Iceberg on."""
    configure_dlt()  # point dlt's iceberg catalog at Polaris (or the local SqlCatalog)

    if is_local():
        from pathlib import Path

        root = Path(settings.iceberg_local_warehouse).resolve()
        root.mkdir(parents=True, exist_ok=True)
        return dlt.destinations.filesystem(bucket_url=root.as_uri())

    return dlt.destinations.filesystem(
        bucket_url=f"s3://{settings.spaces_bucket}",
        credentials={
            "aws_access_key_id": settings.spaces_access_key_id,
            "aws_secret_access_key": settings.spaces_secret_access_key,
            "endpoint_url": settings.spaces_endpoint_url,
            "region_name": settings.spaces_region,
        },
    )


def build_pipeline(*, pipeline_name: str, source: str) -> dlt.Pipeline:
    """A dlt pipeline that lands raw Iceberg tables in namespace ``raw_<source>``.

    dbt reads them as sources (see the generated ``_healthdata_gov__sources.yml``).
    """
    return dlt.pipeline(
        pipeline_name=pipeline_name,
        destination=_filesystem_destination(),
        dataset_name=namespace("raw", source),
        progress=None,
    )
