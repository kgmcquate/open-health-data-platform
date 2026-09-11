# dlt's @source/@resource decorators are untyped and dlt.pipeline()'s overloads
# reject a Destination object in the `destination` position; both are the
# documented usage. Relax only this module.
# mypy: disable-error-code="no-untyped-def, untyped-decorator, call-overload, no-any-return"
"""A ``dlt`` source over one Socrata dataset, landing a native table in `raw`
(ADR-0012). dlt writes straight to the destination — Snowflake in prod, a local
DuckDB file in dev/CI (``ohdp_shared.settings.is_snowflake_configured``) — and
handles schema evolution and the incremental cursor itself; there is no
separate staging step or catalog-commit call. dlt keeps its own pipeline state
in the destination dataset, which is what makes the cursor survive a pod
restart.

Every row carries two system columns we alias in explicitly:

* ``socrata_id``  (``:id``)          — stable per-row key; the ``clean`` layer
  dedupes on it.
* ``socrata_updated_at`` (``:updated_at``) — row mutation time; the incremental
  cursor, so re-runs only *fetch* rows that changed. The raw table itself is
  **append-only** (ADR-0010) — full history of what the API returned — and its
  schema auto-evolves: dlt adds new columns on write, no bespoke code needed.

``$order=:id`` gives a stable pagination key.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Literal

import dlt
from dlt.sources.helpers import requests

from ohdp_ingestion.naming import namespace
from ohdp_shared import get_logger
from ohdp_shared.settings import is_snowflake_configured, settings

log = get_logger(__name__)

WriteDisposition = Literal["append", "replace"]

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

    # With a cursor we only fetched what changed, so `append` — raw is the full
    # history of what the API returned (ADR-0010). Without one we re-fetched the
    # whole dataset, so `replace` rather than append duplicates.
    @dlt.resource(
        name=table_name,
        primary_key="socrata_id",
        write_disposition=write_disposition(incremental_cursor),
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


def write_disposition(incremental_cursor: str | None) -> WriteDisposition:
    """How a run's rows land in the raw table.

    With a cursor we only fetched what changed, so `append` — raw is the full
    history of what the API returned (ADR-0010). Without one we re-fetched the
    whole dataset, so `replace` rather than append duplicates.
    """
    return "append" if incremental_cursor else "replace"


def _destination() -> Any:
    """dlt destination for the raw table: Snowflake in prod, local DuckDB
    otherwise (ADR-0012). dlt writes straight here — schema evolution and the
    incremental cursor's pipeline state are both handled natively."""
    if is_snowflake_configured():
        return dlt.destinations.snowflake(
            credentials={
                "database": settings.snowflake_database,
                "host": settings.snowflake_account,
                "username": settings.snowflake_user,
                "private_key": settings.snowflake_private_key,
                "warehouse": settings.snowflake_warehouse,
                "role": settings.snowflake_role,
            }
        )
    return dlt.destinations.duckdb(credentials=settings.duckdb_path)


def build_pipeline(*, pipeline_name: str, source: str) -> dlt.Pipeline:
    """A dlt pipeline that stages one source's deltas as Parquet.

    ``load_raw_table`` runs it and commits the result to ``raw_<source>``; dbt
    reads the tables as dbt sources (the generated
    ``_healthdata_gov__sources.yml``).
    """
    return dlt.pipeline(
        pipeline_name=pipeline_name,
        destination=_destination(),
        dataset_name=namespace("raw", source),
        progress=None,
    )


@dataclass(frozen=True)
class RawLoad:
    """What one raw load did, for the asset's Dagster metadata."""

    load_ids: list[str]
    rows: int
    strategy: WriteDisposition


def load_raw_table(
    *,
    resource_id: str,
    table_name: str,
    source: str,
    incremental_cursor: str | None = "socrata_updated_at",
    row_limit: int | None = None,
) -> RawLoad:
    """Fetch one Socrata dataset and land it in ``raw_<source>.<table_name>``.

    Extract/normalize/load are run as separate steps (rather than a single
    ``pipeline.run()``) so the load step can be skipped on a zero-row extract —
    running it anyway on a `replace` resource would empty the table on a quiet
    day, which is not the same thing as "the dataset is empty".
    """
    pipeline = build_pipeline(pipeline_name=f"{source}_{table_name}", source=source)
    pipeline.extract(
        socrata_source(
            resource_id,
            table_name,
            incremental_cursor=incremental_cursor,
            row_limit=row_limit,
        )
    )
    rows = pipeline.normalize().row_counts.get(table_name, 0)

    load_ids: list[str] = []
    if rows:
        load_ids = list(pipeline.load().loads_ids)
    else:
        log.info("no new rows extracted; leaving the raw table alone", table=table_name)

    return RawLoad(
        load_ids=load_ids, rows=rows, strategy=write_disposition(incremental_cursor)
    )
