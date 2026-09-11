"""Central settings. Every secret is read from the environment — never hard-coded,
never passed through Dagster run config or tags (ARCHITECTURE.md §5)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

Tier = Literal["free", "paid"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OHDP_", env_file=".env", extra="ignore")

    environment: Literal["local", "staging", "prod"] = "local"
    log_json: bool = True
    log_level: str = "INFO"

    # Object storage (DigitalOcean Spaces, S3-compatible)
    spaces_endpoint_url: str = ""
    spaces_access_key_id: str = ""
    spaces_secret_access_key: str = ""
    spaces_region: str = "nyc3"
    spaces_bucket: str = "ohdp-warehouse"
    snapshot_retention: int = Field(default=14, description="daily snapshots kept in Spaces")

    # Iceberg lakehouse (ADR-0010, catalog per ADR-0011). Snowflake Horizon Catalog
    # is the REST catalog and owns the storage — the table files live in Snowflake,
    # not in spaces_bucket, and Horizon vends short-lived credentials for them.
    iceberg_catalog_uri: str = Field(
        default="",
        description=(
            "Horizon Catalog Iceberg REST endpoint, "
            "https://<org>-<account>.snowflakecomputing.com/polaris/api/catalog"
        ),
    )
    iceberg_catalog_name: str = "ohdp"
    iceberg_credential: str = Field(
        default="",
        description=(
            "OAuth2 client_credentials pair for the catalog: '<snowflake_user>:<pat>'. "
            "Terraform's iceberg_credential output."
        ),
    )
    iceberg_scope: str = Field(
        default="session:role:OHDP_PIPELINE",
        description="Snowflake role the catalog session runs as.",
    )
    iceberg_warehouse: str = Field(
        default="OHDP",
        description=(
            "The Iceberg REST 'warehouse' — a Snowflake *database* name, not a "
            "virtual warehouse. Namespaces are schemas inside it."
        ),
    )
    # When iceberg_catalog_uri is unset (local dev / CI) fall back to a pyiceberg
    # SqlCatalog: this SQLite file is the catalog, iceberg_local_warehouse the data root.
    iceberg_local_catalog_path: str = "data/warehouse/iceberg_catalog.db"
    iceberg_local_warehouse: str = "data/warehouse/lake"

    # Local DuckDB warehouse path used by the publish step (writer side only)
    duckdb_path: str = "data/warehouse/warehouse.duckdb"

    # Postgres (single instance, 4 logical databases)
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    app_database_url: str = ""

    # Semantic layer / catalog
    cube_api_url: str = "http://localhost:4000"
    cube_api_secret: str = ""
    openmetadata_url: str = "http://localhost:8585"
    openmetadata_jwt: str = ""

    # Chat quotas (ARCHITECTURE.md §10 open decision 3 — provisional)
    free_monthly_questions: int = 20
    paid_monthly_questions: int = 500


@lru_cache
def _load() -> Settings:
    return Settings()


settings: Settings = _load()
