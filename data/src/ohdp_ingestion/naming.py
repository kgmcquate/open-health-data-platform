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
same names resolve for Cube over SQL without quoting — and they
are the names those tables already had, since Snowflake folded the old
lower-case dbt project's identifiers to upper case anyway.

:func:`database` is the name each layer's catalog is known by on both sides —
DuckDB's ``ATTACH ... AS RAW`` alias (one per layer, see data/dbt/profiles.yml)
and the Snowflake database itself. They are the same object.
"""

from __future__ import annotations

import re
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


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return re.sub(r"_+", "_", slug) or "dataset"


# A generated raw table's filename cap. dlt derives *filenames* from the table
# name (the extract writer's `<raw_table>.<content hash>.<idx>.typed-jsonl.gz`,
# ~28 bytes of suffix), and Linux caps one path component at 255 bytes
# (NAME_MAX), so an uncapped catalog title fails the whole extract step with
# `OSError [Errno 36] File name too long` before a single row moves. Cap well
# short of NAME_MAX rather than at it: the slack absorbs any collision suffix
# a scraper appends and any future change to dlt's filename layout. 120 is also
# the point where the names stop being paragraphs, and it is deliberately
# loose enough not to rename any table that has already loaded successfully.
_MAX_TABLE_NAME = 120


def table_name(text: str, dataset_id: str = "") -> str:
    """``slugify``, dlt's rule for an identifier that starts with a digit, and a
    length cap (see ``_MAX_TABLE_NAME``).

    A leading digit is not a legal unquoted SQL identifier, and dlt's snake_case
    naming convention silently prefixes one with ``_`` on the way to the
    catalog. Applying the same rule here is what keeps a source's declared
    table name equal to the table dlt actually creates — and with it the
    generated dbt source and the ``lakehouse/`` raw asset key that the load
    reports its materialization against.

    A capped name keeps its leading words — the distinguishing part of a
    catalog title is the front, not the "...during mandatory reporting period
    from august 1 2020 to..." tail — cut at a word boundary so it stays
    readable, and ends with ``dataset_id`` when one is given, since two long
    titles can easily share their first 120 characters.
    """
    slug = slugify(text)
    slug = f"_{slug}" if slug[0].isdigit() else slug
    if len(slug) <= _MAX_TABLE_NAME:
        return slug

    suffix = f"_{dataset_id.replace('-', '_')}" if dataset_id else ""
    cut = _MAX_TABLE_NAME - len(suffix)
    head = slug[:cut]
    if slug[cut] != "_":
        # `head` ends mid-word; drop the partial token rather than emit a stub
        head = head.rpartition("_")[0] or head
    return f"{head.rstrip('_')}{suffix}"
