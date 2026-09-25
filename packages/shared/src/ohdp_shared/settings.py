"""Central settings. Every secret is read from the environment — never hard-coded,
never passed through Dagster run config or tags (ARCHITECTURE.md §5)."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

Tier = Literal["free", "paid"]

# Absolute, not "./.env": every app in this monorepo is run from its own
# subdirectory (`cd apps/api && uv run ...`, per each app's README), and a
# relative path here resolves against that CWD, not the repo root — so it
# silently never found the root .env and every OHDP_* setting loaded empty.
_REPO_ROOT_ENV_FILE = Path(__file__).resolve().parents[4] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OHDP_", env_file=_REPO_ROOT_ENV_FILE, extra="ignore"
    )

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
    # Snowflake reads the very same tables over SQL for Cube.
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
            "users don't accept password auth. What Cube authenticates "
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
    # Public hostname for links users click (e.g. the Hub's Sources page);
    # openmetadata_url above is the internal/cluster address used for REST
    # calls and is not reachable from a browser.
    openmetadata_public_url: str = "https://catalog.open-health-data-platform.org"
    openmetadata_jwt: str = ""
    # Curated literature ingestion (ohdp_ingestion.literature) — both OpenAlex
    # (its "mailto" param) and Europe PMC (its User-Agent) use this same
    # address to self-identify the client, which is what gets a request into
    # OpenAlex's "polite pool"/a materially higher rate limit and is Europe
    # PMC's expected practice for bulk automated queries. Empty falls back to
    # each service's anonymous-client treatment rather than failing.
    # Deliberately not hard-coded here even though it's non-sensitive: every
    # other environment-specific value in this file is env-sourced too, and
    # this repo is public (ARCHITECTURE.md's own framing) — set
    # OHDP_OPENALEX_CONTACT_EMAIL in the deploy's own .env.
    openalex_contact_email: str = ""
    # hub-api's in-cluster address — the only other place the trending
    # literature sync (ohdp_orchestration.assets.literature_trending_sync)
    # writes to: its own `curated_literature` Postgres table stays owned by
    # hub-api (same reasoning as every other table in hub_api.content — "rows
    # are written by us... never by [another service] directly"), so the
    # pipeline calls an internal HTTP route instead of opening its own
    # connection to the `app` database. Same DNS-naming convention as
    # `cube_api_url` above (`<release name>.<namespace>.svc.cluster.local`).
    hub_api_url: str = "http://hub-api.app.svc.cluster.local:8000"
    # Shared bearer token authenticating that internal route — generated by
    # platform-base and injected into both hub-api and the Dagster run
    # launcher, same pattern as `cube_api_secret`/OHDP_CUBE_API_SECRET above
    # (platform/helm/charts/platform-base/templates/secrets.yaml).
    internal_ingest_token: str = ""
    # GraphQL endpoint OpenMetadata's Dagster connector reads pipeline/run
    # metadata from. In-cluster this is graphql-authz-proxy, not the raw
    # dagster-webserver Service (ARCHITECTURE.md §5) — a pod-to-pod caller with
    # none of oauth2-proxy's headers falls into the proxy's public-viewer
    # group, same as dagster-monitoring.
    dagster_graphql_url: str = "http://localhost:3000"

    # OpenMetadata persona FQN whose curated context becomes the system-prompt
    # preamble (docs/chatbot.md §3.1). Empty until personas are seeded (M3.3),
    # and the loop falls back to its built-in prompt when it is.
    chat_persona: str = ""

    # OpenAI-spec chat-completions model backends (docs/chatbot.md §4a).
    # Semicolon-separated, index-aligned lists, one entry per backend —
    # OpenRouter, self-hosted vLLM/Ollama, Azure OpenAI, anything that answers
    # `GET {base_url}/models` and speaks chat-completions. hub-api lists each
    # backend's models at startup and merges them into one picker
    # (hub_api.models); a key can be blank for a backend that needs none
    # (e.g. a local server).
    openai_api_base_urls: str = ""
    openai_api_keys: str = ""

    @property
    def openai_backends(self) -> list[tuple[str, str]]:
        urls = [u.strip() for u in self.openai_api_base_urls.split(";") if u.strip()]
        keys = [k.strip() for k in self.openai_api_keys.split(";")]
        return [(url, keys[i] if i < len(keys) else "") for i, url in enumerate(urls)]

    # Chat quotas (ARCHITECTURE.md §10 open decision 3 — provisional)
    free_daily_questions: int = 10
    paid_daily_questions: int = 50
    # A second, independent gate alongside the question count: a handful of
    # questions can burn very different amounts of model spend, so the thing
    # worth capping is tokens too, not just questions.
    free_daily_tokens: int = 200_000
    paid_daily_tokens: int = 2_000_000
    # `render_dashboard`/`save_dashboard` cost a live Cube query apiece (and a
    # save also costs a catalog publish), on top of whatever tokens the turn
    # itself burns — capped separately so a chart-heavy session can't dodge
    # the token gate by asking the model to draw the same query forty times.
    free_daily_renders: int = 30
    paid_daily_renders: int = 300
    free_daily_saves: int = 2
    paid_daily_saves: int = 20
    # A signed-in reporter's daily cap on *new* issues (hub_api.issues,
    # hub_api.db.issues_today) — separate from `_rate_limited`'s in-process
    # hourly spam ceiling below, which is unauthenticated-safe but too coarse
    # to express "one free report a day": it caps request rate, not identity.
    # A duplicate report (one that matches an already-open issue) doesn't
    # consume this, since it creates nothing new. Anonymous chatbot reporters
    # have no email to key this on, so only the signed-in web path is gated by
    # it — that's fine, they're already the sole occupants of the hourly
    # ceiling's shared "chatbot" bucket.
    free_daily_issues: int = 1
    paid_daily_issues: int = 3

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
    # Bearer token the chat surface's tool connection presents to /tools.
    # Distinct from the browser path, which is identified by the hub's own
    # signed session cookie instead — see hub_api.issues.get_reporter for why
    # those are two different identities and not one.
    tools_auth_token: str = ""

    # Hub sign-in (hub_api.auth). The hub does its own OIDC — there is no
    # oauth2-proxy in front of the app tier. Works with any OIDC provider;
    # Google is the default issuer. `session_secret_key` signs the session
    # cookie and must be set in any deployed environment.
    session_secret_key: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_issuer: str = "https://accounts.google.com"
    # Public base URL of the hub, used to build the OAuth redirect URI.
    hub_base_url: str = "http://localhost:8000"
    # Comma-separated allowlist, checked at /auth/callback. Empty means "allow
    # any verified email" — the state ARCHITECTURE.md §10 plans for once
    # billing (M4) can meter strangers. Until then this is the single control
    # that used to live in oauth2-proxy-app.yaml's `authenticatedEmailsFile`,
    # now that the hub does its own OIDC instead of sitting behind that wall.
    allowed_emails: str = ""
    # Comma-separated list of emails that get the admin role (hub_api.auth's
    # `is_admin`). Config, not a database column, on purpose: an admin can
    # delete published dashboards, so the set of them should be something a
    # deploy changes and a database write cannot — and revoking one takes
    # effect on the next request rather than at their next sign-in, because
    # the role is derived per request instead of being frozen into the session
    # cookie the way `tier` is. Empty means nobody is an admin.
    admin_emails: str = ""

    @property
    def allowed_emails_list(self) -> list[str]:
        return [e.strip().lower() for e in self.allowed_emails.split(",") if e.strip()]

    @property
    def admin_emails_list(self) -> list[str]:
        return [e.strip().lower() for e in self.admin_emails.split(",") if e.strip()]

    # --- Stripe billing (hub_api.billing) ----------------------------------
    # The paid tier is a $5/mo Stripe subscription, sold through hosted
    # Checkout (M4). `stripe_secret_key` is deliberately NOT read under the
    # usual OHDP_ prefix: the deployment already injects it as a bare
    # `STRIPE_SECRET_KEY` (platform/helm + .github/workflows/deploy-platform.yml),
    # so it is looked up under that exact name. Everything else here is
    # non-secret config and follows the normal OHDP_* convention.
    stripe_secret_key: str = Field(
        default="",
        description=(
            "Stripe secret key. Read from the STRIPE_SECRET_KEY environment "
            "variable (no OHDP_ prefix — that is how the deploy injects it)."
        ),
        validation_alias="STRIPE_SECRET_KEY",
    )
    stripe_price_id: str = Field(
        default="",
        description=(
            "The monthly Stripe Price ID sold by POST /api/billing/checkout. "
            "Per-account config (differs between test and live), so env-sourced — "
            "an empty value makes checkout answer 503 rather than half-work."
        ),
    )
    # Pinned API version — Stripe's documented best practice is to pin to the
    # version your integration was written and tested against. This one carries
    # the preview flag that `saved_payment_method_options` needs on the embedded
    # Checkout page (`ui_mode="embedded_page"`). Keep it in lock-step with the
    # js.stripe.com build loaded in apps/web/index.html.
    stripe_api_version: str = "2026-03-25.dahlia; custom_checkout_payment_form_preview=v1"
    # Signs POST /api/billing/webhook/stripe's payload (`stripe.Webhook.construct_event`).
    # Per-endpoint, from the Dashboard's webhook config, not the account's secret
    # key — same "empty means answer 503, not half-work" posture as the other
    # billing settings. No OHDP_ prefix: it sits next to STRIPE_SECRET_KEY, which
    # the deploy already injects bare.
    stripe_webhook_secret: str = Field(
        default="",
        description=(
            "Stripe webhook signing secret. Read from the STRIPE_WEBHOOK_SECRET "
            "environment variable (no OHDP_ prefix, matching STRIPE_SECRET_KEY)."
        ),
        validation_alias="STRIPE_WEBHOOK_SECRET",
    )

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


@lru_cache
def env_file_values() -> dict[str, str]:
    """The repo-root `.env`, as a raw name -> value dict — every name exactly
    as written there (`OHDP_*` and bare names like `CUBE_API_SECRET` alike),
    not just the `OHDP_*` subset `Settings` itself exposes as typed fields.

    For config files that are not `.env` and were never going to get their own
    `Settings` field per entry — `apps/api/config/tools.yaml`'s operator-defined
    tool connections, one example — but still need to resolve a `${NAME}`
    placeholder to a real secret the same way `Settings` already does. Empty
    outside local dev, where there is no `.env` file at all and secrets arrive
    as real environment variables instead — `os.environ` is what a `${NAME}`
    placeholder should fall back to there.
    """
    if not _REPO_ROOT_ENV_FILE.exists():
        return dict(os.environ)
    return {k: v for k, v in dotenv_values(_REPO_ROOT_ENV_FILE).items() if v is not None}
