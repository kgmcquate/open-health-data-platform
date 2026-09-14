"""Medallion-layer naming (ADR-0019) — shared by the raw loader, dbt's schema
config and the Snowflake read side, so names stay in sync everywhere."""

from __future__ import annotations

import pytest

from ohdp_ingestion.naming import catalog, namespace, schema


def test_catalog() -> None:
    # Equal to dbt's `attach.alias`/`database` (data/dbt/profiles.yml) and to
    # the Snowflake catalog-linked database (platform/terraform/snowflake.tf).
    assert catalog() == "lakehouse"


def test_schema() -> None:
    assert schema("raw", "healthdata_gov") == "raw_healthdata_gov"
    assert schema("clean", "cdc") == "clean_cdc"
    assert schema("curated") == "core"
    assert schema("curated", "respiratory") == "mart_respiratory"


def test_schema_requires_a_source_outside_curated() -> None:
    with pytest.raises(ValueError):
        schema("raw")


def test_namespace() -> None:
    assert namespace("raw", "healthdata_gov") == "lakehouse.raw_healthdata_gov"
    assert namespace("clean", "cdc") == "lakehouse.clean_cdc"
    assert namespace("curated") == "lakehouse.core"
    assert namespace("curated", "respiratory") == "lakehouse.mart_respiratory"
