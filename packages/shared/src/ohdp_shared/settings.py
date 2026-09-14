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

    # Object storage (DigitalOcean Spaces, S3-compatible). Postgres backups
    # only — the lakehouse and Dagster's compute logs are on AWS S3 (ADR-0019).
    spaces_endpoint_url: str = ""
    spaces_access_key_id: str = ""
    spaces_secret_access_key: str = ""
    spaces_region: str = "nyc3"
    spaces_bucket: str = "ohdp-warehouse"

    # --- The Iceberg lakehouse: AWS S3 + the Glue Iceberg REST catalog
    # (ADR-0019). dlt writes raw tables through pyiceberg and dbt-duckdb writes
    # clean/core/marts through DuckDB's ATTACH; both authenticate with the same
    # IAM user, and both sign Glue's REST endpoint with SigV4.
    aws_region: str = "us-east-1"
    aws_access_key_id: str = Field(
        default="",
        description=(
            "Pipeline IAM user's access key. Terraform's aws_pipeline_access_key_id "
            "output; also exported as AWS_ACCESS_KEY_ID for boto3/pyiceberg's own "
            "SigV4 signing (see ohdp_ingestion.socrata.source)."
        ),
    )
    aws_secret_access_key: str = ""
    lakehouse_bucket: str = Field(
        default="ohdp-lakehouse",
        description="S3 bucket holding every Iceberg table's data and metadata files.",
    )
    glue_catalog_id: str = Field(
        default="",
        description=(
            "AWS account ID — what the Glue Iceberg REST endpoint calls the catalog "
            "name. Terraform's aws_account_id output."
        ),
    )
    # Namespace names are derived per medallion layer (ohdp_ingestion.naming,
    # ADR-0019), not configured here.

    # Snowflake (ADR-0019). No longer the pipeline's compute: it reads the same
    # Iceberg tables through a catalog-linked database, for Cube's metric
    # queries and Streamlit's ad-hoc ones.
    snowflake_account: str = Field(
        default="",
        description="<organization>-<account> identifier Cube/Streamlit connect to.",
    )
    snowflake_user: str = "OHDP_PIPELINE"
    snowflake_private_key: str = Field(
        default="",
        description=(
            "The query role's RSA private key (PKCS#8 PEM) — Snowflake SERVICE users "
            "don't accept password auth. Terraform's snowflake_private_key output."
        ),
    )
    snowflake_role: str = "OHDP_PIPELINE"
    snowflake_warehouse: str = "OHDP_WH"

    # Postgres (single instance, 4 logical databases)
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    app_database_url: str = ""

    # Semantic layer / catalog
    cube_api_url: str = "http://localhost:4000"
    cube_api_secret: str = ""
    openmetadata_url: str = "http://localhost:8585"
    openmetadata_jwt: str = ""
    # GraphQL endpoint OpenMetadata's Dagster connector reads pipeline/run
    # metadata from. In-cluster this is graphql-authz-proxy, not the raw
    # dagster-webserver Service (ARCHITECTURE.md §5) — a pod-to-pod caller with
    # none of oauth2-proxy's headers falls into the proxy's public-viewer
    # group, same as dagster-monitoring.
    dagster_graphql_url: str = "http://localhost:3000"

    # Chat agent (docs/chatbot.md §4). Read as OHDP_ANTHROPIC_API_KEY, which is
    # deliberately *not* the SDK's own ANTHROPIC_API_KEY: every secret this
    # platform holds arrives through the OHDP_ prefix and one Kubernetes Secret,
    # and an SDK that silently picks a key up from an unprefixed env var would
    # be the one exception (§5).
    anthropic_api_key: str = ""
    # OpenMetadata persona FQN whose curated context becomes the system-prompt
    # preamble (docs/chatbot.md §3.1). Empty until personas are seeded (M3.3),
    # and the loop falls back to its built-in prompt when it is.
    chat_persona: str = ""

    # Chat quotas (ARCHITECTURE.md §10 open decision 3 — provisional)
    free_monthly_questions: int = 20
    paid_monthly_questions: int = 500

    @property
    def glue_rest_uri(self) -> str:
        """Glue's Iceberg REST endpoint for `aws_region`. The one URI dlt
        (pyiceberg), DuckDB's `ATTACH` and Snowflake's catalog integration all
        point at."""
        return f"https://glue.{self.aws_region}.amazonaws.com/iceberg"

    @property
    def lakehouse_url(self) -> str:
        """`s3://` root every Iceberg table's files live under."""
        return f"s3://{self.lakehouse_bucket}"


@lru_cache
def _load() -> Settings:
    return Settings()


settings: Settings = _load()
