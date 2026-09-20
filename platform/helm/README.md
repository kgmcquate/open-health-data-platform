# Helm

Charts we own, and values files for the upstream charts.

```
charts/
  platform-base/        our chart — namespaces, generated internal secrets,
                        the shared Postgres StatefulSet, the ClusterIssuer
  hub-api/              our chart — the FastAPI backend, chat UI, and its own
                        OIDC sign-in (hub_api.auth); owns app.open-health-data-platform.org's
                        Ingress directly, no oauth2-proxy wall in front
  graphql-authz-proxy/  our chart — wraps kgmcquate/graphql-authz-proxy
  dagster-monitoring/   our chart — wraps kgmcquate/dagster-monitoring
  cube/                 our chart — Cube Core, the semantic layer (ADR-0003)
  cubestore/            our chart — Cube Store, Cube's pre-aggregation cache
                        (ADR-0024). Deploy before/with cube.
  mcp-cube/             our chart — Cube's tool surface over MCP, for Open WebUI
                        (ADR-0017). Runs the hub-api image with a different command.
values/
  traefik.yaml                for traefik/traefik
  cert-manager.yaml           for jetstack/cert-manager
  dagster.yaml                for dagster/dagster
  openmetadata.yaml           for open-metadata/openmetadata  (pinned to 2.0.x)
  opensearch.yaml             for opensearch/opensearch
  oauth2-proxy.yaml           for oauth2-proxy/oauth2-proxy  (Google wall → Dagster + dagster-monitoring, ADR-0007)
  open-webui.yaml             for open-webui/open-webui      (the chat UI, ADR-0017 — Google SSO is its own, not a wall)
```

There is no data warehouse here. It is Snowflake, created by
`platform/terraform/snowflake.tf` and reached over the internet — nothing to
install, nothing to bootstrap ([ADR-0012](../../docs/decisions/0012-native-snowflake-tables.md)).

Every upstream chart version is pinned in the `Makefile` (`*_VERSION`).
`helm upgrade --install` otherwise pulls the newest and runs its migrations
unattended — bump a pin only after reading the upstream changelog. OpenMetadata
runs its Flyway DB migrations from an initContainer on every `make openmetadata`.

## Why Dagster and OpenMetadata are values files, not charts

Writing our own charts for these would be a mistake. Both projects ship
maintained charts that encode non-obvious wiring — Dagster's webserver/daemon/
run-launcher/user-deployment split and its Job-per-run launcher; OpenMetadata's
migration job, JWT config and search bootstrap. Vendoring that means redoing it
on every upgrade, for no gain. We own the *values*, which is where all our
actual decisions live. See [ADR-0006](../../docs/decisions/0006-upstream-charts-and-external-authz-proxy.md).

`hub-api`, `graphql-authz-proxy`, and `dagster-monitoring` get
real charts because nothing upstream exists for them.

## Prerequisites

- A cluster from [`platform/terraform`](../terraform) with `KUBECONFIG` exported.

### Secrets

`make infra` (the `platform-base` chart) generates every **internal** secret and
keeps it stable across upgrades:

| Secret | Namespace | Holds |
|---|---|---|
| `postgres-secret` | `infra` | postgres + the 4 database passwords (`dagster`, `openmetadata`, `app`, `openwebui`) |
| `dagster-postgresql-secret` | `data` | mirror of the dagster password |
| `openmetadata-db-auth` | `meta` | mirror of the openmetadata password |
| `openmetadata-fernet-secret` | `meta` | OpenMetadata fernet key |
| `cube-secret` | `data` | Cube API shared secret (`CUBEJS_API_SECRET`) — read by Cube itself, Dagster's run-launcher + `ohdp-pipeline` user-deployment (`openmetadata_cube_metrics_sync`), and mirrored into `hub-api-db` for hub-api |
| `hub-api-db` | `app` | `OHDP_APP_DATABASE_URL`, `OHDP_CUBE_API_SECRET` |
| `openwebui-db` | `app` | Open WebUI's `DATABASE_URL` + `WEBUI_SECRET_KEY` (ADR-0017) |
| `mcp-cube-secret` | `app` | `OHDP_CUBE_API_SECRET` + the `OHDP_MCP_AUTH_TOKEN` bearer token Open WebUI presents to mcp-cube |
| `oauth2-proxy-secret` | `data` | oauth2-proxy `cookie-secret` for Dagster + dagster-monitoring (Google `client-id`/`client-secret` merged in externally) |
| `ohdp-pipeline-config` (ConfigMap) | `data` | non-secret pipeline env |

The **external** secrets are GitHub Actions repo secrets, injected by the
`secrets` step of [`deploy-platform.yml`](../../.github/workflows/deploy-platform.yml).
For a manual deploy, create them yourself after `make base`:

```bash
# The Spaces endpoint + bucket are non-secret (data/ohdp-pipeline-config).
kubectl -n data create secret generic ohdp-pipeline-secrets \
  --from-literal=OHDP_SPACES_ACCESS_KEY_ID=... \
  --from-literal=OHDP_SPACES_SECRET_ACCESS_KEY=... \
  --from-literal=OHDP_OPENAQ_API_KEY=... \
  --from-literal=OHDP_CDC_APP_TOKEN=... \
  --from-literal=OHDP_OPENMETADATA_JWT= \        # fill after OpenMetadata's first boot
  --from-literal=OHDP_SNOWFLAKE_PRIVATE_KEY="$(cd ../terraform && terraform output -raw snowflake_private_key)"

# OHDP_SNOWFLAKE_PRIVATE_KEY is the query role's RSA private key (PKCS#8 PEM) —
# Snowflake SERVICE users don't accept password auth, so this is what Cube's
# driver authenticates with, generated by Terraform (ADR-0019).
# The non-secret half — account, user, role, warehouse — is in
# data/ohdp-pipeline-config, so when you `terraform apply` a new account,
# update `pipelineConfig` in charts/platform-base/values.yaml to match the
# `snowflake_*` outputs. The pipeline's *catalog* credential is a different
# thing again — OHDP_SNOWFLAKE_PAT, in the same Secret, for the Iceberg REST
# endpoint (ADR-0019).

# Cube queries Snowflake directly with the same read-only role/key (ADR-0019):
kubectl -n data create secret generic cube-snowflake \
  --from-literal=OHDP_SNOWFLAKE_PRIVATE_KEY="$(cd ../terraform && terraform output -raw snowflake_private_key)"

# OHDP_OIDC_CLIENT_ID/_SECRET are hub-api's own OIDC client (hub_api.auth) — it
# does the Authorization Code flow itself rather than sitting behind an
# oauth2-proxy wall. Reusing Dagster's Google client is fine as long as that
# client also lists https://app.open-health-data-platform.org/auth/callback
# among its authorized redirect URIs.
kubectl -n app create secret generic hub-api-secrets \
  --from-literal=OHDP_OPENMETADATA_JWT= \
  --from-literal=STRIPE_SECRET_KEY= \
  --from-literal=OHDP_OIDC_CLIENT_ID= \
  --from-literal=OHDP_OIDC_CLIENT_SECRET=

# Open WebUI (ADR-0017). OPENAI_API_KEY holds the *OpenRouter* key (ADR-0023):
# OpenRouter is OpenAI-compatible at https://openrouter.ai/api/v1, and this
# surface runs z-ai/glm-5.3-flash there rather than Claude direct — hub-api's
# ANTHROPIC_API_KEY is a different key for a different service now.
# GOOGLE_CLIENT_SECRET belongs to Open WebUI's own OAuth client — redirect URI
# https://chat.open-health-data-platform.org/oauth/google/callback — not to
# Dagster's oauth2-proxy wall or hub-api's own OIDC client below.
kubectl -n app create secret generic openwebui-secrets \
  --from-literal=OPENAI_API_KEY="$OPENROUTER_API_KEY" \
  --from-literal=GOOGLE_CLIENT_SECRET="$OPENWEBUI_OIDC_CLIENT_SECRET"

# oauth2-proxy (ADR-0007) — merge the Google client creds into the Secret
# platform-base created (it already holds the generated `cookie-secret`).
# `make base` must have run first. Google Cloud console: a "Web application"
# OAuth client, redirect URI https://dagster.open-health-data-platform.org/oauth2/callback
kubectl -n data patch secret oauth2-proxy-secret --type merge -p "$(printf \
  '{"data":{"client-id":"%s","client-secret":"%s"}}' \
  "$(printf %s "$DAGSTER_OIDC_CLIENT_ID" | base64 -w0)" \
  "$(printf %s "$DAGSTER_OIDC_CLIENT_SECRET" | base64 -w0)")"
```

`OHDP_OPENMETADATA_JWT` is minted by OpenMetadata itself (Settings → Bots →
`ingestion-bot`); leave it empty for the first deploy, then patch both secrets
and restart the consumers.

## Install

```bash
make repos                # add + update upstream helm repos
make infra                 # platform-base, Traefik, cert-manager, ClusterIssuer
# create the external secrets (above)
make install                # opensearch, openmetadata, proxy, dagster
make dagster-monitoring      # the /monitoring dashboard (before oauth2-proxy, below)
make oauth2-proxy           # Google wall: Dagster + dagster-monitoring
make hub-api                # chat UI + /api — does its own OIDC sign-in, no oauth2-proxy wall
make mcp-cube                # Cube's tool surface over MCP (before open-webui)
OPENWEBUI_OIDC_CLIENT_ID=... make open-webui   # the chat UI (ADR-0017)
```

`make open-webui` needs `OPENWEBUI_OIDC_CLIENT_ID` — the Google client ID, whose
matching secret is in `openwebui-secrets`. It is not a secret, but it is passed
in rather than written into `values/open-webui.yaml` so that both halves
obviously come from the same client.

**Open WebUI needs one manual step after install.** MCP tool servers are stored
in its own database with no declarative env var, so register `mcp-cube` by hand:
Settings → Admin → Integrations → External Tool Servers → Add Connection, type
*MCP (Streamable HTTP)*, URL `http://mcp-cube.app.svc.cluster.local:8000/mcp`,
auth *Bearer* with `kubectl -n app get secret mcp-cube-secret -o
jsonpath='{.data.OHDP_MCP_AUTH_TOKEN}' | base64 -d`. See
`charts/mcp-cube/templates/NOTES.txt` and
[ADR-0017](../../docs/decisions/0017-open-webui-chat-ui.md).

Order matters — OpenSearch must be green before OpenMetadata starts or its
migration job fails (`make install` sequences this for you).

The warehouse is not installed here: `terraform apply` in `platform/terraform`
creates the Snowflake database, schemas, role, service user and token, and the
pipeline connects directly over SQL. Inspection is Snowsight — there is no
in-cluster admin UI (ADR-0012).

`mcp-cube` runs the **hub-api image** with its command overridden, so both
releases must be deployed at the same `TAG` — the MCP tool surface and the chat
agent's tool surface are the same code, and building them from different commits
is the drift ADR-0016 warns about.

`dagster` and `hub-api` run `ghcr.io/kgmcquate/ohdp-{pipeline,hub-api}`
at the `sha-<12>` tag of the current commit — `TAG` defaults to `git rev-parse
HEAD`, which `build-images.yml` pushes on every merge to `main`. Pass
`TAG=sha-…` to pin an older build. Never `latest`: the hub-api chart refuses
it, so a pod
restart or `helm rollback` always lands on a known image.

## Before trusting the proxy

`make verify-proxy` sends a mutation and expects a 403. Run it after every
Dagster upgrade — the allowlist is by root field name, and new UI versions
issue new queries.
