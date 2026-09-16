# dlt's @source/@resource decorators are untyped, dlt.pipeline()'s overloads
# reject a Destination object in the `destination` position, and `columns=`
# rejects the documented `None` (opt out of hints); all are documented usage.
# Relax only this module.
# mypy: disable-error-code="no-untyped-def,untyped-decorator,call-overload,no-any-return,arg-type"
"""A ``dlt`` source over one Socrata dataset — any domain (ADR-0018) — landing
an Iceberg table in ``RAW.<SOURCE>``: files in our S3 bucket, catalogued
through Snowflake's Horizon REST endpoint (ADR-0013 for the layout, ADR-0019
for the catalog). There is no bespoke stage-then-commit step the way ADR-0010
needed — schema evolution and the incremental cursor are handled by the
destination in ``_destination`` below, which also keeps dlt's own pipeline
state, so the cursor survives a pod restart. See that function's docstring
for why a hand-rolled destination is needed at all, rather than dlt's
built-in ``filesystem`` + Iceberg support.

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

import dataclasses
import os
from collections.abc import Iterator, Sequence
from typing import Any, Final, Literal

import dlt
import pyarrow.parquet as pq
from dlt.common.configuration import configspec
from dlt.common.data_types import TDataType
from dlt.common.destination import (
    Destination,
    DestinationCapabilitiesContext,
    PreparedTableSchema,
)
from dlt.common.destination.client import (
    DestinationClientConfiguration,
    JobClientBase,
    LoadJob,
    StateInfo,
    StorageSchemaInfo,
    WithStateSync,
)
from dlt.common.libs.pyiceberg import get_catalog, write_iceberg_table
from dlt.common.schema.typing import TColumnSchema
from dlt.destinations.job_impl import FinalizedLoadJob
from dlt.sources.helpers import requests
from pyiceberg.exceptions import NoSuchTableError

from ohdp_ingestion import naming
from ohdp_ingestion.socrata.config import ColumnSpec
from ohdp_ingestion.socrata.domain import SocrataDomain
from ohdp_shared.settings import settings

WriteDisposition = Literal["append", "replace"]

_SYSTEM_SELECT = "*,:id AS socrata_id,:updated_at AS socrata_updated_at"
_EPOCH = "1900-01-01T00:00:00.000"
_MAX_PAGE = 50_000  # Socrata hard ceiling on $limit
_INT64_MIN = -(2**63)  # widest integer any destination (or dlt's JSON writer) accepts
_INT64_MAX = 2**63 - 1

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
    (decimal point, exponent, ...) becomes `float` (dlt: double).

    Python ints are unbounded but no destination's integer type is: dlt's JSON
    writer (orjson) rejects anything outside signed 64-bit with "Integer
    exceeds 64-bit range", and `bigint` could not hold it regardless. A Socrata
    `number` that large is always a magnitude, never an identifier — CDC
    `atcp-73re` carries a few ~2.7e21 `site_wval` outliers — so widen it to
    `float`/double and keep the load moving rather than failing extract.
    """
    try:
        parsed = int(value)
    except ValueError:
        return float(value)
    return parsed if _INT64_MIN <= parsed <= _INT64_MAX else float(parsed)


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


def socrata_source(
    socrata: SocrataDomain,
    resource_id: str,
    table_name: str,
    *,
    incremental_cursor: str | None = "socrata_updated_at",
    row_limit: int | None = None,
    page_size: int = 5_000,
    app_token: str | None = None,
    columns: Sequence[ColumnSpec] | None = None,
) -> Any:
    """A single-resource ``dlt`` source for ``resource_id`` on ``socrata``.

    ``incremental_cursor`` only controls the SoQL ``$where`` (fetch efficiency);
    set it to ``None`` to re-fetch the whole dataset each run (correct for
    datasets Socrata rewrites wholesale). ``columns`` is the catalog's declared
    schema (``DatasetConfig.columns``) — see ``_column_hints`` for why it's
    needed at all.

    ``@dlt.source`` is applied here rather than to this function itself because
    its ``name`` — which becomes the dlt *schema* name recorded in the
    destination's pipeline state, and so must stay stable per domain — is only
    known once ``socrata`` is in hand.
    """
    base_url = f"https://{socrata.domain}/resource/{resource_id}.json"
    token = app_token or os.environ.get(socrata.app_token_env) or None
    headers = {"X-App-Token": token} if token else {}
    page = min(page_size, _MAX_PAGE)
    number_columns = (
        frozenset(c.name for c in columns if c.type == "number") if columns else frozenset()
    )

    @dlt.source(name=socrata.source)
    def _source():
        # With a cursor we only fetched what changed, so `append` — raw is the full
        # history of what the API returned (ADR-0010). Without one we re-fetched the
        # whole dataset, so `replace` rather than append duplicates.
        @dlt.resource(
            name=table_name,
            primary_key="socrata_id",
            write_disposition=write_disposition(incremental_cursor),
            columns=_column_hints(columns),
            # What makes the filesystem destination commit an Iceberg table
            # (registered in Horizon) rather than bare Parquet files.
            table_format="iceberg",
        )
        # dlt's documented pattern is to default the arg to the incremental object;
        # B008 flags the call in the default but that is exactly how the hint is wired.
        #
        # `range_start="open"` matches the SoQL `$where` below, which is already
        # strictly `>` the last cursor value — boundary rows are never re-fetched,
        # so dlt's boundary deduplication has nothing to do. Left on (its default
        # `"closed"`) it stores a primary-key hash for *every* row whose cursor
        # equals `last_value`, in the pipeline state. Socrata bulk-rewrites a
        # dataset with one uniform `:updated_at` — CDC `2ew6-ywp6` is archived with
        # all 837k rows at a single timestamp — which puts the whole table on the
        # boundary and blows the state past Snowflake's 16 MB VARCHAR ceiling
        # (`_dlt_pipeline_state` load fails with SQL 100074).
        def rows(
            cursor: dlt.sources.incremental[str] | None = (
                dlt.sources.incremental(  # noqa: B008
                    incremental_cursor, initial_value=_EPOCH, range_start="open"
                )
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
                    raise TypeError(
                        f"{resource_id}: expected a JSON array, got {type(batch).__name__}"
                    )
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

    return _source()


def write_disposition(incremental_cursor: str | None) -> WriteDisposition:
    """How a run's rows land in the raw table.

    With a cursor we only fetched what changed, so `append` — raw is the full
    history of what the API returned (ADR-0010). Without one we re-fetched the
    whole dataset, so `replace` rather than append duplicates.
    """
    return "append" if incremental_cursor else "replace"


@configspec
class HorizonIcebergConfiguration(DestinationClientConfiguration):
    """Minimal config spec for ``_destination``'s custom destination — no
    resolvable fields of its own (everything it needs is closed over per
    ``source``), just a fixed ``destination_type`` so dlt's configuration
    resolution has one (the base class leaves it ``None``, which
    ``resolve_configuration`` then rejects as an unresolved field)."""

    destination_type: Final[str] = dataclasses.field(  # type: ignore[misc]
        default="horizon_iceberg", init=False, repr=False, compare=False
    )


def _destination(source: str) -> Any:
    """dlt destination for the raw table (ADR-0019): a hand-rolled Iceberg
    writer committing through Snowflake's Horizon catalog, plus a
    ``WithStateSync`` implementation backed by two more Iceberg tables.

    **Every table** — data and dlt's own bookkeeping tables alike — is
    written the same way: read the parquet file dlt already staged locally,
    create/evolve (``union_by_name``) and append the Iceberg table via
    pyiceberg (``get_catalog``/``write_iceberg_table``), registering it in
    Horizon. dlt's own ``filesystem`` destination has native Iceberg support
    that does the same thing, but its ``get_open_table_catalog``
    unconditionally calls ``catalog.create_namespace()`` the first time it
    touches a dataset, and Horizon's REST catalog 404s there instead of
    raising ``NamespaceAlreadyExistsError`` — namespaces are
    Terraform-managed (ADR-0021), not created by ingestion clients. Hence the
    manual writer below, which only ever calls ``create_table_if_not_exists``.
    A pleasant side effect of going through pyiceberg for everything: every
    write, bookkeeping tables included, rides Horizon's vended storage
    credentials (``header.X-Iceberg-Access-Delegation`` below) — no static
    AWS key needed anywhere in this module.

    **Reading dlt's bookkeeping tables back** is the part a
    ``@dlt.destination`` sink function can't do at all: that decorator
    produces a client that only implements ``JobClientBase``, not
    ``WithStateSync`` — it can write state forward but has no read path. dlt's
    ``Pipeline._restore_state_from_destination`` checks for ``WithStateSync``
    and silently gives up when it's missing ("Destination does not support
    state sync"), so ``restore_from_destination`` never actually restores
    anything: the incremental cursor resets to ``_EPOCH`` and every run
    re-fetches the whole dataset. This class fixes that by implementing
    ``WithStateSync``'s three read methods as a pyiceberg table scan over
    ``_dlt_pipeline_state``/``_dlt_version``, picking the newest matching row.
    """
    # The catalog's `warehouse` is already the RAW database (see
    # `iceberg_catalog_config`), so the identifier only needs the bare schema
    # name here — `naming.namespace` would double up the database, giving
    # pyiceberg a 3-part identifier ("RAW.CDC.<table>") that it splits into
    # namespace ("RAW", "CDC") instead of just ("CDC",), 404ing against Horizon.
    namespace = naming.schema("raw", source)

    def _catalog() -> Any:
        # Load the catalog using the same config we publish to dlt.config.
        return get_catalog(
            iceberg_catalog_type="rest", iceberg_catalog_config=iceberg_catalog_config()
        )

    def _write_iceberg(table_name: str, arrow: Any, write_disposition: str) -> None:
        catalog = _catalog()
        identifier = f"{namespace}.{table_name}"
        tbl = catalog.create_table_if_not_exists(identifier, arrow.schema)
        # `create_table_if_not_exists` only sets the schema on first creation —
        # an existing table's schema is left as-is, so a later batch with a
        # new column (Socrata datasets grow columns without warning, see the
        # module docstring) fails `table.append()`'s strict schema check
        # unless the table's schema is evolved to match first.
        tbl.update_schema().union_by_name(arrow.schema).commit()
        write_iceberg_table(table=tbl, data=arrow, write_disposition=write_disposition)

    def _newest_row(
        table_name: str, filters: dict[str, Any], order_by: str
    ) -> dict[str, Any] | None:
        """The most recent row (by ``order_by``, a timestamp column) matching
        every ``filters`` entry, or ``None`` if the table doesn't exist yet
        (first-ever run) or nothing matches. Bookkeeping tables get one row
        per run, so a full scan stays cheap indefinitely."""
        try:
            table = _catalog().load_table(f"{namespace}.{table_name}")
        except NoSuchTableError:
            return None
        rows = table.scan().to_arrow().to_pylist()
        matching = [row for row in rows if all(row.get(f) == v for f, v in filters.items())]
        if not matching:
            return None
        return max(matching, key=lambda row: row[order_by])

    def _schema_info(
        row: dict[str, Any] | None, naming_convention: Any
    ) -> StorageSchemaInfo | None:
        return StorageSchemaInfo.from_normalized_mapping(row, naming_convention) if row else None

    class _HorizonIcebergClient(JobClientBase, WithStateSync):
        def initialize_storage(self, truncate_tables: Any = None) -> None:
            pass

        def is_storage_initialized(self) -> bool:
            return True

        def drop_storage(self) -> None:
            pass

        def complete_load(self, load_id: str) -> None:
            pass

        def __enter__(self) -> _HorizonIcebergClient:
            return self

        def __exit__(self, *exc_info: Any) -> None:
            pass

        def create_load_job(
            self, table: PreparedTableSchema, file_path: str, load_id: str, restore: bool = False
        ) -> LoadJob:
            try:
                arrow = pq.read_table(file_path)
                _write_iceberg(table["name"], arrow, table.get("write_disposition", "append"))
                return FinalizedLoadJob(file_path)
            except Exception as exc:  # noqa: BLE001 — surfaced as a failed load job, not raised
                return FinalizedLoadJob(
                    file_path, status="failed", failed_message=str(exc), exception=exc
                )

        def get_stored_state(self, pipeline_name: str) -> StateInfo | None:
            naming_convention = self.schema.naming
            row = _newest_row(
                self.schema.state_table_name,
                {naming_convention.normalize_identifier("pipeline_name"): pipeline_name},
                naming_convention.normalize_identifier("created_at"),
            )
            if row is None:
                return None
            return StateInfo.from_normalized_mapping(row, naming_convention)

        def get_stored_schema(self, schema_name: str | None = None) -> StorageSchemaInfo | None:
            naming_convention = self.schema.naming
            filters = (
                {naming_convention.normalize_identifier("schema_name"): schema_name}
                if schema_name
                else {}
            )
            row = _newest_row(
                self.schema.version_table_name,
                filters,
                naming_convention.normalize_identifier("inserted_at"),
            )
            return _schema_info(row, naming_convention)

        def get_stored_schema_by_hash(  # type: ignore[override]
            self, version_hash: str
        ) -> StorageSchemaInfo | None:
            # dlt's own ABC declares a non-Optional return here despite its
            # docstring allowing None; dlt's own implementations (filesystem,
            # sql, ...) return None too when nothing is found.
            naming_convention = self.schema.naming
            row = _newest_row(
                self.schema.version_table_name,
                {naming_convention.normalize_identifier("version_hash"): version_hash},
                naming_convention.normalize_identifier("inserted_at"),
            )
            return _schema_info(row, naming_convention)

    class _HorizonIcebergDestination(
        Destination[HorizonIcebergConfiguration, _HorizonIcebergClient]
    ):
        def _raw_capabilities(self) -> DestinationCapabilitiesContext:
            caps = DestinationCapabilitiesContext.generic_capabilities("parquet")
            caps.supported_loader_file_formats = ["parquet"]
            caps.supports_ddl_transactions = False
            caps.supports_transactions = False
            caps.naming_convention = "direct"
            caps.max_table_nesting = 100
            caps.max_parallel_load_jobs = 0
            caps.loader_parallelism_strategy = None
            return caps

        @property
        def spec(self) -> type[HorizonIcebergConfiguration]:
            return HorizonIcebergConfiguration

        @property
        def client_class(self) -> type[_HorizonIcebergClient]:
            return _HorizonIcebergClient

    return _HorizonIcebergDestination()


def iceberg_catalog_config() -> dict[str, Any]:
    """pyiceberg ``load_catalog`` kwargs for Snowflake's Horizon REST catalog.

    ``warehouse`` is the catalog's name, which for Horizon is the Snowflake
    database — here ``RAW``, the only one the loader writes to
    (``ohdp_ingestion.naming.database``). The clean and curated layers are
    separate catalogs, attached separately by dbt.

    Auth is OAuth2 client-credentials with a programmatic access token,
    exchanged at Horizon's token endpoint. ``credential`` must be the *bare*
    PAT, not ``"<user>:<pat>"`` — a colon-bearing credential makes pyiceberg's
    legacy OAuth2 manager split it into a ``client_id``/``client_secret`` pair
    and send both, and Snowflake's token endpoint 400s with ``invalid_scope:
    The scope is invalid`` the moment a ``client_id`` rides along (ADR-0011).
    ``scope`` is not optional either — the same ``invalid_scope`` error is what
    ADR-0012 hit omitting ``session:role:<ROLE>``.

    The access-delegation header asks Horizon to vend short-lived storage
    credentials for reads. ADR-0010 had to *suppress* this header, because
    Polaris could not subscope credentials for non-AWS storage; on S3 with
    Snowflake as the catalog it is the supported path.
    """
    return {
        "type": "rest",
        "uri": settings.horizon_catalog_uri,
        "warehouse": naming.database("raw"),
        "credential": settings.snowflake_pat,
        "oauth2-server-uri": settings.horizon_oauth_uri,
        "scope": settings.horizon_scope,
        "header.X-Iceberg-Access-Delegation": "vended-credentials",
    }


def configure_catalog() -> None:
    """Publish the catalog config and the naming convention into dlt's config
    providers.

    The filesystem destination resolves its catalog lazily, deep inside the
    load (``FilesystemClient.get_open_table_catalog`` -> ``get_catalog``), with
    no argument we can thread through from here — so the configuration has to
    arrive the way every other dlt setting does. Writing to ``dlt.config``
    rather than a ``secrets.toml``/``.pyiceberg.yaml`` file keeps it derived
    from ``ohdp_shared.settings`` (§5: secrets come from the environment) and
    leaves nothing to mount into the run pod.

    ``schema.naming`` is what makes dlt create ``RAW_CDC.NNDSS_WEEKLY_DATA``
    rather than its default lower case — see ``ohdp_ingestion.sql_upper``.
    """
    dlt.config["schema.naming"] = "ohdp_ingestion.sql_upper"
    dlt.config["iceberg_catalog.iceberg_catalog_name"] = naming.database("raw")
    dlt.config["iceberg_catalog.iceberg_catalog_type"] = "rest"
    dlt.config["iceberg_catalog.iceberg_catalog_config"] = iceberg_catalog_config()


def build_pipeline(*, pipeline_name: str, source: str) -> dlt.Pipeline:
    """A dlt pipeline that lands one source's deltas as an Iceberg table.

    Used to build a ``@dlt_assets``-decorated asset (see
    ``ohdp_orchestration.components.socrata``), which runs it and commits the
    result to ``RAW.<SOURCE>`` (ADR-0013); dbt reads the tables as
    dbt sources (the generated ``_stg_<source>__sources.yml``).
    """
    configure_catalog()
    return dlt.pipeline(
        pipeline_name=pipeline_name,
        destination=_destination(source),
        dataset_name=naming.schema("raw", source),
        progress=None,
    )
