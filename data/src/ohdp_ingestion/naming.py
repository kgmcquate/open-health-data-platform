"""Medallion-layer naming (ADR-0019), shared by the raw loader, dbt's schema
config and the Snowflake read side, so names stay in sync everywhere. Pure
string logic, no catalog/warehouse dependency.

One Iceberg catalog (AWS Glue, reached over its Iceberg REST endpoint) holds
every table. Glue namespaces are flat and lowercase, so the medallion layer is
carried by a *prefix on the namespace* rather than by a separate database the
way ADR-0013 had it:

    raw_<source>     raw_cdc, raw_healthdata_gov     written by dlt
    clean_<source>   clean_cdc                       written by dbt-duckdb
    core             conformed, cross-source         written by dbt-duckdb
    mart_<name>      mart_respiratory                written by dbt-duckdb

:func:`catalog` is the name both engines know the catalog by — the DuckDB
``ATTACH ... AS lakehouse`` alias dbt writes through, and the Snowflake
catalog-linked database Cube and Streamlit read through. Keeping them equal is
what lets a single dbt manifest describe relations both engines can resolve
(``lakehouse.core.hospital_utilization_daily``).
"""

from __future__ import annotations

from typing import Literal

Layer = Literal["raw", "clean", "curated"]

# The DuckDB ATTACH alias and the Snowflake catalog-linked database name. These
# must stay equal — see the module docstring. Mirrored in dbt's profiles.yml
# (`attach.alias`) and platform/terraform/aws.tf's catalog-linked database.
CATALOG = "lakehouse"

_LAYER_PREFIXES: dict[Layer, str] = {"raw": "raw_", "clean": "clean_", "curated": "mart_"}


def catalog() -> str:
    """Name of the Iceberg catalog as both engines address it."""
    return CATALOG


def schema(layer: Layer, source: str | None = None) -> str:
    """Iceberg namespace (a Glue database; a schema to DuckDB and Snowflake).

    raw/clean -> ``<layer>_<source>`` (e.g. "clean_cdc");
    curated   -> ``mart_<mart>``, or "core" when no mart is given.
    """
    if layer == "curated":
        return f"mart_{source}" if source else "core"
    if not source:
        raise ValueError(f"layer {layer!r} needs a source")
    return f"{_LAYER_PREFIXES[layer]}{source}"


def namespace(layer: Layer, source: str | None = None) -> str:
    """Fully qualified ``catalog.namespace``, e.g. ``"lakehouse.raw_cdc"`` or
    ``"lakehouse.core"``."""
    return f"{catalog()}.{schema(layer, source)}"
