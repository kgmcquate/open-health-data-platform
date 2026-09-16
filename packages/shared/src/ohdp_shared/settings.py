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

    # --- The Iceberg lakehouse (ADR-0019). Snowflake is the *catalog* only:
    # dlt and dbt-duckdb write Iceberg tables through Horizon's Iceberg REST
    # endpoint, and the files land in our own S3 bucket via an external volume.
    # Snowflake reads the very same tables over SQL for Cube and Streamlit.
    #
    # Two credentials, because the two protocols authenticate differently:
    # a PAT for the REST catalog (below) and the RSA key pair for the SQL
    # connector (further down).
    snowflake_account: str = Field(
        default="",
        description=(
            "<organization>-<account> identifier. Also what the Horizon REST "
            "endpoint is addressed by — see horizon_catalog_uri."
        ),
    )
    snowflake_pat: str = Field(
        default="",
        description=(
            "Programmatic access token for the Horizon Iceberg REST catalog. This "
            "is the pipeline's *write* credential — dlt (pyiceberg) and dbt "
            "(DuckDB) both exchange it for an OAuth2 access token. Terraform's "
            "snowflake_pipeline_pat output. PATs require the user to carry a "
            "network policy, which snowflake.tf attaches."
        ),
    )
    aws_region: str = "us-east-1"
    lakehouse_bucket: str = Field(
        default="ohdp-lakehouse",
        description=(
            "S3 bucket behind the external volume. Snowflake decides where inside "
            "it each table's files go; this is only needed by dlt, which writes "
            "its Parquet there directly."
        ),
    )
    aws_access_key_id: str = Field(
        default="",
        description=(
            "Pipeline IAM user's access key, for writing data files into the "
            "external volume's bucket. Terraform's aws_pipeline_access_key_id "
            "output. DuckDB can instead take catalog-vended credentials; dlt's "
            "fsspec writer cannot, so this stays."
        ),
    )
    aws_secret_access_key: str = ""
    # Namespace names are derived per medallion layer (ohdp_ingestion.naming,
    # ADR-0019), not configured here.

    snowflake_user: str = "OHDP_PIPELINE"
    snowflake_private_key: str = Field(
        default="",
        description=(
            "RSA private key (PKCS#8 PEM) for the SQL connector — Snowflake SERVICE "
            "users don't accept password auth. What Cube and Streamlit authenticate "
            "with. Terraform's snowflake_private_key output."
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

    # Issue reporting (hub_api.issues). The token is a fine-grained PAT with
    # issues:write on `github_issues_repo` and nothing else — it is handed to a
    # tool a chat user can trigger, so its scope *is* the blast radius. Both
    # empty by default: the endpoint answers 503 rather than half-working, so a
    # deployment that has not been given a token fails visibly.
    github_token: str = ""
    github_issues_repo: str = Field(
        default="",
        description="owner/name of the repository issues are filed in.",
    )
    # Bearer token Open WebUI presents to /tools. Distinct from the browser path,
    # which is identified by oauth2-proxy's X-Forwarded-Email instead — see
    # hub_api.issues.get_reporter for why those are two different identities and
    # not one.
    tools_auth_token: str = ""

    @property
    def horizon_catalog_uri(self) -> str:
        """Snowflake Horizon's Iceberg REST endpoint — the one URI both dlt
        (pyiceberg) and DuckDB's `ATTACH` point at. Apache Polaris is what
        serves it inside Horizon, hence the path."""
        return f"https://{self.snowflake_account}.snowflakecomputing.com/polaris/api/catalog"

    @property
    def horizon_oauth_uri(self) -> str:
        """Token endpoint the PAT is exchanged at, for the OAuth2
        client-credentials flow both clients use."""
        return f"{self.horizon_catalog_uri}/v1/oauth/tokens"

    @property
    def horizon_scope(self) -> str:
        """OAuth2 scope Snowflake's token endpoint requires: the role the
        resulting session runs as.

        Getting this wrong is not a quiet failure — it is the `invalid_scope`
        that ADR-0012 spent a round on before abandoning the catalog.
        """
        return f"session:role:{self.snowflake_role}"

    @property
    def lakehouse_url(self) -> str:
        """`s3://` root of the external volume's bucket."""
        return f"s3://{self.lakehouse_bucket}"


@lru_cache
def _load() -> Settings:
    return Settings()


settings: Settings = _load()
