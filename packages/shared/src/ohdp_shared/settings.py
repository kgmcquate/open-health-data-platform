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

    # Snowflake data warehouse (ADR-0012/0014). Plain tables, no REST catalog.
    snowflake_account: str = Field(
        default="",
        description=(
            "<organization>-<account> identifier dlt/dbt-snowflake connect to as host/account."
        ),
    )
    snowflake_user: str = "OHDP_PIPELINE"
    snowflake_private_key: str = Field(
        default="",
        description=(
            "The pipeline's RSA private key (PKCS#8 PEM) — Snowflake SERVICE users "
            "don't accept password auth, so this is what both dlt's Snowflake "
            "destination and dbt-snowflake authenticate with. Terraform's "
            "snowflake_private_key output."
        ),
    )
    snowflake_role: str = "OHDP_PIPELINE"
    snowflake_warehouse: str = "OHDP_WH"
    # Database names are fixed per medallion layer (ohdp_ingestion.naming,
    # ADR-0013), not configured here — RAW is what dlt's raw loader connects to.

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
