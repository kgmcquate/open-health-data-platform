"""Medallion-layer naming (ADR-0013), shared by the raw loader and dbt's
schema/database config, so names stay in sync across the write and read
sides. Pure string logic, no destination/warehouse dependency.

Snowflake gets one database per layer (RAW/CLEAN/CURATED) with a schema per
source or mart.
"""

from __future__ import annotations

from typing import Literal

Layer = Literal["raw", "clean", "curated"]

_DATABASES: dict[Layer, str] = {"raw": "raw", "clean": "clean", "curated": "curated"}


def database(layer: Layer) -> str:
    """Snowflake database for a medallion layer: raw -> RAW, clean -> CLEAN,
    curated -> CURATED."""
    return _DATABASES[layer]


def schema(layer: Layer, source: str | None = None) -> str:
    """Schema name within a layer's database.

    raw/clean -> ``<source>`` (e.g. "healthdata_gov");
    curated   -> ``<mart>`` (source treated as the mart name), or "core" when
    no mart is given.
    """
    if layer == "curated":
        return source or "core"
    if not source:
        raise ValueError(f"layer {layer!r} needs a source")
    return source


def namespace(layer: Layer, source: str | None = None) -> str:
    """Fully qualified ``DATABASE.schema`` for a layer, e.g.
    ``"RAW.healthdata_gov"`` or ``"CURATED.core"``."""
    return f"{database(layer)}.{schema(layer, source)}"
