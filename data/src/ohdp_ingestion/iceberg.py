"""One place that knows how to reach the Iceberg catalog (ADR-0010, ADR-0011).

Production: Snowflake's **Horizon Catalog** over the Iceberg REST protocol
(``OHDP_ICEBERG_CATALOG_URI`` set). Local dev / CI: a pyiceberg ``SqlCatalog`` on
SQLite with a local warehouse dir — same ``pyiceberg.catalog`` API, no services.

Snowflake owns the storage for these tables and vends short-lived credentials
to the REST client per table, which is why nothing here passes ``s3.*``
properties or picks table locations. DuckDB is still the only compute; Snowflake
is a catalog and a bucket.

Used by three callers, so it lives in the ingestion layer (no dagster/dlt import):
- ``ohdp_ingestion.healthdata_gov`` — the raw loader
- ``ohdp_ingestion.dbt.iceberg`` — the dbt read/write plugin
- ``ohdp_orchestration`` — the publish step
"""

from __future__ import annotations

import warnings
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

import pyarrow as pa

from ohdp_shared import get_logger
from ohdp_shared.settings import settings

log = get_logger(__name__)

# Medallion layers. `raw` and `clean` namespaces are suffixed with the source;
# `curated` splits into `core` and `mart_<name>`.
Layer = str  # "raw" | "clean" | "curated"

# How a commit lands against whatever is already in the table.
Strategy = Literal["overwrite", "append", "upsert"]
STRATEGIES: tuple[str, ...] = ("overwrite", "append", "upsert")


def is_local() -> bool:
    return not settings.iceberg_catalog_uri


def catalog_properties() -> dict[str, Any]:
    """The kwargs for ``pyiceberg.catalog.load_catalog``."""
    if is_local():
        db = Path(settings.iceberg_local_catalog_path).resolve()
        db.parent.mkdir(parents=True, exist_ok=True)
        warehouse = Path(settings.iceberg_local_warehouse).resolve()
        warehouse.mkdir(parents=True, exist_ok=True)
        return {
            "type": "sql",
            "uri": f"sqlite:///{db}",
            "warehouse": warehouse.as_uri(),
        }

    props: dict[str, Any] = {
        "type": "rest",
        # Horizon's `warehouse` is a Snowflake *database*; namespaces are its
        # schemas. Not a virtual warehouse — that is unrelated and unused here.
        "warehouse": settings.iceberg_warehouse or settings.iceberg_catalog_name,
        "uri": settings.iceberg_catalog_uri,
        # OAuth2 client_credentials against <uri>/v1/oauth/tokens. The credential
        # is "<snowflake_user>:<pat>" and the scope names the role the catalog
        # session assumes — both come straight from Terraform outputs.
        "scope": settings.iceberg_scope,
        # Take Snowflake up on credential vending: it hands back per-table,
        # time-limited storage credentials with the table metadata, so the
        # pipeline never holds a key for the lake's bucket. This is the default
        # pyiceberg behaviour, stated explicitly because the Polaris setup this
        # replaced had to suppress it.
        "header.X-Iceberg-Access-Delegation": "vended-credentials",
    }
    if settings.iceberg_credential:
        props["credential"] = settings.iceberg_credential
    return props


def load_catalog() -> Any:
    """Return a live pyiceberg ``Catalog``."""
    from pyiceberg.catalog import load_catalog as _load

    return _load(settings.iceberg_catalog_name, **catalog_properties())


def namespace(layer: Layer, source: str | None = None) -> str:
    """Iceberg namespace for a layer.

    raw/clean -> ``<layer>_<source>`` (``raw_healthdata_gov``);
    curated   -> ``core`` or ``mart_<source>`` (source treated as the mart name).

    Lowercase, and Terraform creates the matching Snowflake schemas lowercase to
    agree with it (platform/terraform/snowflake.tf). The REST protocol passes
    these through verbatim, so they are quoted identifiers in Snowflake SQL.
    """
    if layer == "curated":
        return f"mart_{source}" if source else "core"
    if not source:
        raise ValueError(f"layer {layer!r} needs a source")
    return f"{layer}_{source}"


def _align(data: pa.Table, schema: pa.Schema) -> pa.Table:
    """Project `data` onto `schema`: add missing columns as nulls, drop extras,
    order to match. Callers evolve the table first so `schema` is the superset."""
    cols = {}
    for field in schema:
        cols[field.name] = (
            data.column(field.name).cast(field.type)
            if field.name in data.column_names
            else pa.nulls(data.num_rows, field.type)
        )
    return pa.table(cols, schema=schema)


def commit(
    data: pa.Table,
    table_id: str,
    *,
    strategy: Strategy = "overwrite",
    join_cols: Sequence[str] | None = None,
    catalog: Any | None = None,
) -> None:
    """Land `data` in the Iceberg table `table_id` ("<namespace>.<table>").

    Creates the namespace and table if absent, evolves the schema to the union
    of old and new columns, then writes. This is the *only* write path into the
    lake — both the raw loader and the dbt plugin come through here, so schema
    drift behaves identically in every layer (ADR-0010: ~150 scraped datasets
    whose columns move without warning).

    No `location` is ever passed on create: Snowflake-managed storage assigns
    the table location itself and rejects one from the client.
    """
    if strategy not in STRATEGIES:
        raise ValueError(f"strategy must be one of {STRATEGIES}, got {strategy!r}")

    from pyiceberg.exceptions import NoSuchTableError

    cat = catalog if catalog is not None else load_catalog()
    ns = table_id.rsplit(".", 1)[0]
    cat.create_namespace_if_not_exists(ns)

    created = False
    try:
        table = cat.load_table(table_id)
    except NoSuchTableError:
        table = cat.create_table(table_id, schema=data.schema)
        created = True

    schema_grew = not set(data.schema.names) <= set(table.schema().column_names)

    # Evolve to the superset of columns, then reload a fresh handle and project
    # the incoming data onto that schema so every write path casts.
    with table.update_schema() as update:
        update.union_by_name(data.schema)
    table = cat.load_table(table_id)
    data = _align(data, table.schema().as_arrow())

    if strategy == "append":
        table.append(data)
    elif strategy == "upsert":
        if not join_cols:
            raise ValueError("strategy='upsert' needs join_cols")
        if schema_grew and not created and len(table.scan().to_arrow()) > 0:
            # pyiceberg's upsert reads matched rows via a batch reader that does
            # not backfill columns added after a data file was written. Rewrite
            # the existing data under the evolved schema first.
            table.overwrite(table.scan().to_arrow())
            table = cat.load_table(table_id)
        table.upsert(data, join_cols=list(join_cols))
    else:  # overwrite
        with warnings.catch_warnings():
            # "Delete operation did not match any records" on a fresh table.
            warnings.filterwarnings("ignore", message="Delete operation did not match")
            table.overwrite(data)

    log.info(
        "committed to iceberg",
        table=table_id,
        strategy=strategy,
        rows=data.num_rows,
        created=created,
        schema_grew=schema_grew,
    )
