"""Medallion-layer naming (ADR-0013) — shared by the raw loader and dbt's
schema/database config, so names stay in sync across the write and read sides."""

from __future__ import annotations

from ohdp_ingestion.naming import database, namespace, schema


def test_database() -> None:
    assert database("raw") == "RAW"
    assert database("clean") == "CLEAN"
    assert database("curated") == "CURATED"


def test_schema() -> None:
    assert schema("raw", "healthdata_gov") == "healthdata_gov"
    assert schema("clean", "cdc") == "cdc"
    assert schema("curated") == "core"
    assert schema("curated", "respiratory") == "respiratory"


def test_namespace() -> None:
    assert namespace("raw", "healthdata_gov") == "RAW.healthdata_gov"
    assert namespace("clean", "cdc") == "CLEAN.cdc"
    assert namespace("curated") == "CURATED.core"
    assert namespace("curated", "respiratory") == "CURATED.respiratory"
