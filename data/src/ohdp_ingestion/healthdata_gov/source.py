# dlt's @source/@resource decorators are untyped, dlt.pipeline()'s overloads
# reject a Destination object in the `destination` position, and `columns=`
# rejects the documented `None` (opt out of hints); all are documented usage.
# Relax only this module.
# mypy: disable-error-code="no-untyped-def,untyped-decorator,call-overload,no-any-return,arg-type"
"""A ``dlt`` source over one Socrata dataset, landing a native table in the
``RAW`` database (ADR-0013, building on ADR-0014). dlt writes straight to
Snowflake and handles schema evolution and the incremental cursor itself;
there is no separate staging step or catalog-commit call. dlt keeps its own
pipeline state in the destination dataset, which is what makes the cursor
survive a pod restart.

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
from collections.abc import Iterator, Sequence
from typing import Any, Literal

import dlt
from dlt.common.data_types import TDataType
from dlt.common.schema.typing import TColumnSchema
from dlt.sources.helpers import requests

from ohdp_ingestion import naming
from ohdp_ingestion.healthdata_gov.config import ColumnSpec
from ohdp_shared.settings import settings

WriteDisposition = Literal["append", "replace"]

_SYSTEM_SELECT = "*,:id AS socrata_id,:updated_at AS socrata_updated_at"
_EPOCH = "1900-01-01T00:00:00.000"
_MAX_PAGE = 50_000  # Socrata hard ceiling on $limit

# Socrata's SODA API serializes every field as a JSON string, numbers included
# (https://dev.socrata.com/docs/datatypes/ — done to dodge client float-precision
# surprises), so dlt's runtime-type inference sees `str` for everything and lands
# genuinely numeric/boolean/date columns as `text` in the raw table. We already
# have the catalog's declared type per column (`ColumnSpec.type`), so hint dlt's
# normalizer explicitly instead of relying on inference; its coerce dispatch
# (`dlt/common/data_types/type_helpers.py`) then parses the strings for us at
# load time. Left unmapped on purpose: types dlt already gets right as text
# (url, email, location, ...) stay inferred rather than forced.
#
# "number" is deliberately absent: Socrata's catalog has no int/float
# distinction (every numeric column, whole or fractional, is just "number"),
# so a fixed hint here can only ever pick double or bigint for *all* such
# columns, not per-column. Instead `_parse_number_columns` below converts each
# "number" field's string to a real Python `int`/`float` before it reaches
# dlt, and dlt's own value-type inference (`PY_TYPE_TO_SC_TYPE` in
# `type_helpers.py`) does bigint vs. double from that native type — the same
# mechanism it uses for every other source. A column that's all whole numbers
# lands as bigint; if a later row brings a decimal, dlt's existing variant
# machinery spins off a `<col>__v_double` sibling rather than silently
# widening or erroring.
_SOCRATA_TO_DLT_TYPE: dict[str, TDataType] = {
    "money": "decimal",
    "percent": "double",
    "checkbox": "bool",
    "flag": "bool",
    "calendar_date": "timestamp",
    "date": "timestamp",
    "floating_timestamp": "timestamp",
    "fixed_timestamp": "timestamp",
}


def _column_hints(columns: Sequence[ColumnSpec] | None) -> dict[str, TColumnSchema] | None:
    if not columns:
        return None
    hints: dict[str, TColumnSchema] = {
        c.name: {"data_type": _SOCRATA_TO_DLT_TYPE[c.type]}
        for c in columns
        if c.type in _SOCRATA_TO_DLT_TYPE
    }
    return hints or None


def _parse_number(value: str) -> int | float:
    """A bare integer literal becomes `int` (dlt: bigint); anything else
    (decimal point, exponent, ...) becomes `float` (dlt: double)."""
    try:
        return int(value)
    except ValueError:
        return float(value)


def _parse_number_columns(batch: list[dict[str, Any]], number_columns: frozenset[str]) -> None:
    """Mutates `batch` in place, parsing each `number`-typed field from
    Socrata's JSON-string encoding into a native Python int/float so dlt's own
    type inference (not a fixed hint) picks bigint vs. double per column."""
    if not number_columns:
        return
    for row in batch:
        for name in number_columns:
            v = row.get(name)
            if isinstance(v, str) and v != "":
                row[name] = _parse_number(v)


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
    columns: Sequence[ColumnSpec] | None = None,
):
    """A single-resource source for ``resource_id``, written append-only.

    ``incremental_cursor`` only controls the SoQL ``$where`` (fetch efficiency);
    set it to ``None`` to re-fetch the whole dataset each run (correct for
    datasets Socrata rewrites wholesale). ``columns`` is the catalog's declared
    schema (``DatasetConfig.columns``) — see ``_column_hints`` for why it's
    needed at all.
    """
    base_url = f"https://{domain}/resource/{resource_id}.json"
    token = app_token or os.environ.get("OHDP_HEALTHDATA_APP_TOKEN") or None
    headers = {"X-App-Token": token} if token else {}
    page = min(page_size, _MAX_PAGE)
    number_columns = (
        frozenset(c.name for c in columns if c.type == "number") if columns else frozenset()
    )

    # With a cursor we only fetched what changed, so `append` — raw is the full
    # history of what the API returned (ADR-0010). Without one we re-fetched the
    # whole dataset, so `replace` rather than append duplicates.
    @dlt.resource(
        name=table_name,
        primary_key="socrata_id",
        write_disposition=write_disposition(incremental_cursor),
        columns=_column_hints(columns),
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
            _parse_number_columns(batch, number_columns)
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
    """dlt destination for the raw table (ADR-0014). dlt writes straight to
    Snowflake — schema evolution and the incremental cursor's pipeline state
    are both handled natively."""
    return dlt.destinations.snowflake(
        credentials={
            "database": naming.database("raw"),
            "host": settings.snowflake_account,
            "username": settings.snowflake_user,
            "private_key": settings.snowflake_private_key,
            "warehouse": settings.snowflake_warehouse,
            "role": settings.snowflake_role,
        }
    )


def build_pipeline(*, pipeline_name: str, source: str) -> dlt.Pipeline:
    """A dlt pipeline that stages one source's deltas as Parquet.

    Used to build a ``@dlt_assets``-decorated asset (see
    ``ohdp_orchestration.defs.healthdata_gov.component``), which runs it and
    commits the result to ``RAW.<source>`` (ADR-0013); dbt reads the tables as
    dbt sources (the generated ``_healthdata_gov__sources.yml``).
    """
    return dlt.pipeline(
        pipeline_name=pipeline_name,
        destination=_destination(),
        dataset_name=naming.schema("raw", source),
        progress=None,
    )
