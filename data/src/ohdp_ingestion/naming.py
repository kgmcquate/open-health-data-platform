"""Medallion-layer naming, shared by the raw loader and dbt's schema config
(ADR-0012). Pure string logic, no destination/warehouse dependency."""

from __future__ import annotations

from typing import Literal

Layer = Literal["raw", "clean", "curated"]


def namespace(layer: Layer, source: str | None = None) -> str:
    """Schema name for a layer.

    raw/clean -> ``<layer>_<source>`` (``raw_healthdata_gov``);
    curated   -> ``core`` or ``mart_<source>`` (source treated as the mart name).
    """
    if layer == "curated":
        return f"mart_{source}" if source else "core"
    if not source:
        raise ValueError(f"layer {layer!r} needs a source")
    return f"{layer}_{source}"
