"""Medallion-layer naming (ADR-0013, kept by ADR-0019) — shared by the raw
loader, dbt's database/schema config and the Snowflake read side, so names stay
in sync everywhere.

Upper case is the contract, not an accident: Snowflake requires an external
engine reaching it through the Horizon REST catalog to address the database,
namespaces and tables in all capitals."""

from __future__ import annotations

import pytest

from ohdp_ingestion.naming import database, namespace, schema


def test_database() -> None:
    # Each is a Snowflake database, an Iceberg catalog, and a DuckDB `ATTACH`
    # alias (data/dbt/profiles.yml, platform/terraform/snowflake.tf).
    assert database("raw") == "RAW"
    assert database("clean") == "CLEAN"
    assert database("curated") == "CURATED"


def test_schema() -> None:
    assert schema("raw", "healthdata_gov") == "HEALTHDATA_GOV"
    assert schema("clean", "cdc") == "STG_CDC"
    assert schema("curated") == "CORE"
    assert schema("curated", "respiratory") == "RESPIRATORY"


def test_schema_requires_a_source_outside_curated() -> None:
    with pytest.raises(ValueError):
        schema("raw")


def test_namespace() -> None:
    assert namespace("raw", "healthdata_gov") == "RAW.HEALTHDATA_GOV"
    assert namespace("clean", "cdc") == "CLEAN.STG_CDC"
    assert namespace("curated") == "CURATED.CORE"
    assert namespace("curated", "respiratory") == "CURATED.RESPIRATORY"
