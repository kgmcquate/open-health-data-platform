"""Medallion-layer naming (ADR-0013, carried forward by ADR-0019), shared by the
raw loader, dbt's database/schema config and the Snowflake read side, so names
stay in sync everywhere. Pure string logic, no catalog/warehouse dependency.

Snowflake is the Iceberg *catalog* — the table files live in our own S3 bucket
(an external volume), not in Snowflake. A Snowflake database is a catalog and
its schemas are that catalog's namespaces, which means ADR-0013's layout
survives intact rather than being flattened into one namespace per
layer+source:

    RAW.<SOURCE>          RAW.CDC, RAW.HEALTHDATA_GOV     written by dlt
    CLEAN.STG_<SOURCE>    CLEAN.STG_CDC                   written by dbt-duckdb
    CURATED.CORE          conformed, cross-source         written by dbt-duckdb
    CURATED.<MART>        CURATED.RESPIRATORY             written by dbt-duckdb

**Everything is upper case**, which is not a style choice: Snowflake requires
an external engine reaching it through the Horizon REST catalog to address the
database, namespaces and tables in all capitals, whatever case they were
created with. It is also Snowflake's own unquoted-identifier convention, so the
same names resolve for Cube and Streamlit over SQL without quoting — and they
are the names those tables already had, since Snowflake folded the old
lower-case dbt project's identifiers to upper case anyway.

:func:`database` is the name each layer's catalog is known by on both sides —
DuckDB's ``ATTACH ... AS RAW`` alias (one per layer, see data/dbt/profiles.yml)
and the Snowflake database itself. They are the same object.
"""

from __future__ import annotations

from typing import Literal

Layer = Literal["raw", "clean", "curated"]

# One database per medallion layer, each its own Iceberg catalog. Mirrored in
# dbt's profiles.yml (`attach`), dbt_project.yml (`+database`) and
# platform/terraform/snowflake.tf.
_DATABASES: dict[Layer, str] = {"raw": "RAW", "clean": "CLEAN", "curated": "CURATED"}


def database(layer: Layer) -> str:
    """Snowflake database — and Iceberg catalog — for a medallion layer:
    raw -> RAW, clean -> CLEAN, curated -> CURATED."""
    return _DATABASES[layer]


def schema(layer: Layer, source: str | None = None) -> str:
    """Namespace within a layer's catalog, upper-cased.

    raw     -> ``<SOURCE>`` (e.g. "CDC");
    clean   -> ``STG_<SOURCE>``, the dbt-labs staging convention the model
               names already carry (e.g. "STG_CDC");
    curated -> ``<MART>``, or "CORE" when no mart is given.
    """
    if layer == "curated":
        return source.upper() if source else "CORE"
    if not source:
        raise ValueError(f"layer {layer!r} needs a source")
    return f"STG_{source.upper()}" if layer == "clean" else source.upper()


def namespace(layer: Layer, source: str | None = None) -> str:
    """Fully qualified ``DATABASE.SCHEMA``, e.g. ``"RAW.CDC"`` or
    ``"CURATED.CORE"``."""
    return f"{database(layer)}.{schema(layer, source)}"
