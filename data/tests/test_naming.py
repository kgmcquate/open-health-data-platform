"""Medallion-layer naming (ADR-0012) — shared by the raw loader and dbt's
schema config, so schema names stay in sync across the write and read sides."""

from __future__ import annotations

from ohdp_ingestion.naming import namespace


def test_namespaces() -> None:
    assert namespace("raw", "healthdata_gov") == "raw_healthdata_gov"
    assert namespace("clean", "cdc") == "clean_cdc"
    assert namespace("curated") == "core"
    assert namespace("curated", "respiratory") == "mart_respiratory"
