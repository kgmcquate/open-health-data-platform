# dlt's @source/@resource decorators are untyped, dlt.pipeline()'s overloads
# reject a Destination object in the `destination` position, and `columns=`
# rejects the documented `None` (opt out of hints); all are documented usage.
# mypy: disable-error-code="no-untyped-def,untyped-decorator,call-overload,no-any-return,arg-type"
"""The ``dlt`` destination every raw-layer source writes through: a
hand-rolled Iceberg writer that commits through Snowflake's Horizon REST
catalog (ADR-0011, ADR-0013, ADR-0019).

Originally lived only in :mod:`ohdp_ingestion.socrata.source`, written for
HealthData.gov and shared with data.cdc.gov (ADR-0018). Pulled out here once
CMS needed the same landing mechanics over a plain REST source instead of
Socrata's SODA API — everything below is about *where rows land*, nothing
about *how they're fetched*, so it has no Socrata dependency at all. A
source-specific module supplies the ``dlt`` source (extraction); this module
supplies the pipeline that lands it (ADR-0026).

**Every table** — data and dlt's own bookkeeping tables alike — is written the
same way: read the parquet file dlt already staged locally, create/evolve
(``union_by_name``) and append the Iceberg table via pyiceberg
(``get_catalog``/``write_iceberg_table``), registering it in Horizon. dlt's
own ``filesystem`` destination has native Iceberg support that does the same
thing, but its ``get_open_table_catalog`` unconditionally calls
``catalog.create_namespace()`` the first time it touches a dataset, and
Horizon's REST catalog 404s there instead of raising
``NamespaceAlreadyExistsError`` — namespaces are Terraform-managed (ADR-0021),
not created by ingestion clients. Hence the manual writer below, which only
ever calls ``create_table_if_not_exists``. A pleasant side effect of going
through pyiceberg for everything: every write, bookkeeping tables included,
rides Horizon's vended storage credentials
(``header.X-Iceberg-Access-Delegation`` below) — no static AWS key needed
anywhere in this module.

**Reading dlt's bookkeeping tables back** is the part a ``@dlt.destination``
sink function can't do at all: that decorator produces a client that only
implements ``JobClientBase``, not ``WithStateSync`` — it can write state
forward but has no read path. dlt's ``Pipeline._restore_state_from_destination``
checks for ``WithStateSync`` and silently gives up when it's missing
("Destination does not support state sync"), so ``restore_from_destination``
never actually restores anything: an incremental cursor resets to its initial
value and every run re-fetches the whole dataset. This class fixes that by
implementing ``WithStateSync``'s three read methods as a pyiceberg table scan
over ``_dlt_pipeline_state``/``_dlt_version``, picking the newest matching row.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Final, Literal

import dlt
import pyarrow.parquet as pq
from dlt.common.configuration import configspec
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
from dlt.destinations.job_impl import FinalizedLoadJob
from pyiceberg.exceptions import NoSuchTableError

from ohdp_ingestion import naming
from ohdp_shared.settings import settings

WriteDisposition = Literal["append", "replace"]


def write_disposition(incremental_cursor: str | None) -> WriteDisposition:
    """How a run's rows land in the raw table.

    With a cursor we only fetched what changed, so `append` — raw is the full
    history of what the API returned (ADR-0010). Without one we re-fetched the
    whole dataset, so `replace` rather than append duplicates.
    """
    return "append" if incremental_cursor else "replace"


@configspec
class HorizonIcebergConfiguration(DestinationClientConfiguration):
    """Minimal config spec for ``horizon_iceberg_destination``'s custom
    destination — no resolvable fields of its own (everything it needs is
    closed over per ``source``), just a fixed ``destination_type`` so dlt's
    configuration resolution has one (the base class leaves it ``None``,
    which ``resolve_configuration`` then rejects as an unresolved field)."""

    destination_type: Final[str] = dataclasses.field(  # type: ignore[misc]
        default="horizon_iceberg", init=False, repr=False, compare=False
    )


def horizon_iceberg_destination(source: str) -> Any:
    """dlt destination landing every table for ``source`` in ``RAW.<SOURCE>``.

    See the module docstring for why this is a hand-rolled writer rather than
    dlt's built-in ``filesystem`` Iceberg support.
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
        # new column (a source growing columns without warning) fails
        # `table.append()`'s strict schema check unless the table's schema is
        # evolved to match first.
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
    """A dlt pipeline that lands one source's deltas as an Iceberg table in
    ``RAW.<source>`` (ADR-0013); dbt reads the tables as dbt sources (the
    generated ``_stg_<source>__sources.yml``).

    Used to build a ``@dlt_assets``-decorated asset — see
    ``ohdp_orchestration.components.socrata`` (Socrata sources) and
    ``ohdp_orchestration.defs.cms.component`` (CMS).
    """
    configure_catalog()
    return dlt.pipeline(
        pipeline_name=pipeline_name,
        destination=horizon_iceberg_destination(source),
        dataset_name=naming.schema("raw", source),
        progress=None,
    )
