# dlt's @source/@resource decorators are untyped and dlt.pipeline()'s overloads
# reject a Destination object in the `destination` position; both are the
# documented usage. Relax only this module.
# mypy: disable-error-code="no-untyped-def, untyped-decorator, call-overload, no-any-return"
"""A ``dlt`` source over one Socrata dataset, landing an Iceberg table in `raw`.

dlt extracts, normalizes and keeps the incremental cursor; it does **not** write
Iceberg. Its ``filesystem`` destination is a *staging* area holding only the
current run's delta (``write_disposition="replace"``), which
``ohdp_ingestion.iceberg.commit`` then lands in the catalog — the same writer the
dbt plugin uses, so schema drift behaves the same in every layer.

The split exists because dlt's Iceberg destination passes an explicit
``location=`` on create, and Snowflake-managed storage assigns table locations
itself and rejects one from the client (ADR-0011). Staging on Spaces is also
where dlt keeps its pipeline state, which is what makes the incremental cursor
survive a pod restart.

Every row carries two system columns we alias in explicitly:

* ``socrata_id``  (``:id``)          — stable per-row key; the ``clean`` layer
  dedupes on it.
* ``socrata_updated_at`` (``:updated_at``) — row mutation time; the incremental
  cursor, so re-runs only *fetch* rows that changed. The raw table itself is
  **append-only** (ADR-0010) — full history of what the API returned — and its
  schema auto-evolves (``commit`` runs ``union_by_name`` on every write).

``$order=:id`` gives a stable pagination key.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import dlt
import pyarrow as pa
from dlt.sources.helpers import requests

from ohdp_ingestion.iceberg import Strategy, commit, is_local, namespace
from ohdp_shared import get_logger
from ohdp_shared.settings import settings

log = get_logger(__name__)

_SYSTEM_SELECT = "*,:id AS socrata_id,:updated_at AS socrata_updated_at"
_EPOCH = "1900-01-01T00:00:00.000"
_MAX_PAGE = 50_000  # Socrata hard ceiling on $limit
# Staging lives beside the snapshots in Spaces, under its own prefix so it is
# obvious that nothing here is durable.
_STAGING_PREFIX = "_dlt_staging"


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

    # Staging always `replace`: it holds this run's delta and nothing else, so
    # the Iceberg commit can read the whole staged table back. History lives in
    # Iceberg, not here. Whether that commit appends or overwrites is decided by
    # `iceberg_strategy` below, off the same `incremental_cursor` flag.
    @dlt.resource(
        name=table_name,
        primary_key="socrata_id",
        write_disposition="replace",
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


def iceberg_strategy(incremental_cursor: str | None) -> Strategy:
    """How a staged delta lands in the raw Iceberg table.

    With a cursor we only fetched what changed, so `append` — raw is the full
    history of what the API returned (ADR-0010). Without one we re-fetched the
    whole dataset, so `overwrite` rather than append duplicates; Iceberg keeps
    the prior snapshots for time travel either way.
    """
    return "append" if incremental_cursor else "overwrite"


def _staging_destination(source: str) -> Any:
    """dlt ``filesystem`` destination for the staging delta + the pipeline state.

    Not the lake: the durable tables live in the catalog's own storage. This is
    scratch that happens to need to outlive the pod, which is why it is on
    Spaces in prod rather than a local dir.
    """
    if is_local():
        from pathlib import Path

        root = Path(settings.iceberg_local_warehouse).resolve() / "_staging"
        root.mkdir(parents=True, exist_ok=True)
        return dlt.destinations.filesystem(bucket_url=root.as_uri())

    return dlt.destinations.filesystem(
        bucket_url=f"s3://{settings.spaces_bucket}/{_STAGING_PREFIX}/{source}",
        credentials={
            "aws_access_key_id": settings.spaces_access_key_id,
            "aws_secret_access_key": settings.spaces_secret_access_key,
            "endpoint_url": settings.spaces_endpoint_url,
            "region_name": settings.spaces_region,
        },
    )


def build_pipeline(*, pipeline_name: str, source: str) -> dlt.Pipeline:
    """A dlt pipeline that stages one source's deltas as Parquet.

    ``load_raw_table`` runs it and commits the result to ``raw_<source>``; dbt
    reads the Iceberg tables as sources (the generated
    ``_healthdata_gov__sources.yml``).
    """
    return dlt.pipeline(
        pipeline_name=pipeline_name,
        destination=_staging_destination(source),
        dataset_name=namespace("raw", source),
        progress=None,
    )


@dataclass(frozen=True)
class RawLoad:
    """What one raw load did, for the asset's Dagster metadata."""

    load_ids: list[str]
    rows: int
    strategy: Strategy


def load_raw_table(
    *,
    resource_id: str,
    table_name: str,
    source: str,
    incremental_cursor: str | None = "socrata_updated_at",
    row_limit: int | None = None,
) -> RawLoad:
    """Fetch one Socrata dataset and land it in ``raw_<source>.<table_name>``.

    Two steps on purpose (see the module docstring): dlt stages the delta, then
    the shared Iceberg writer commits it.
    """
    pipeline = build_pipeline(pipeline_name=f"{source}_{table_name}", source=source)
    info = pipeline.run(
        socrata_source(
            resource_id,
            table_name,
            incremental_cursor=incremental_cursor,
            row_limit=row_limit,
        )
    )

    strategy = iceberg_strategy(incremental_cursor)
    # dlt's dataset accessor is untyped; `replace` above means this is exactly
    # the rows this run fetched.
    staged: pa.Table = pipeline.dataset()[table_name].arrow()
    if staged.num_rows:
        commit(staged, f"{namespace('raw', source)}.{table_name}", strategy=strategy)
    else:
        # Nothing changed upstream. An `overwrite` here would empty the table on
        # a quiet day, which is not the same thing as "the dataset is empty".
        log.info("no new rows staged; leaving the raw table alone", table=table_name)

    return RawLoad(load_ids=list(info.loads_ids), rows=staged.num_rows, strategy=strategy)
