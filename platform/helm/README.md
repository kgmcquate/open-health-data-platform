# Helm

Charts we own, and values files for the upstream charts.

```
charts/
  platform-base/        our chart — namespaces, generated internal secrets,
                        the shared Postgres StatefulSet, the ClusterIssuer
  hub-api/              our chart — the FastAPI backend
  graphql-authz-proxy/  our chart — wraps kgmcquate/graphql-authz-proxy
values/
  traefik.yaml          for traefik/traefik
  cert-manager.yaml     for jetstack/cert-manager
  dagster.yaml          for dagster/dagster
  openmetadata.yaml     for open-metadata/openmetadata  (pinned to 2.0.x)
  opensearch.yaml       for opensearch/opensearch
  oauth2-proxy.yaml     for oauth2-proxy/oauth2-proxy  (Google wall → Dagster, ADR-0007)
```

There is no Iceberg catalog here. It is Snowflake's Horizon Catalog, created by
`platform/terraform/snowflake.tf` and reached over the internet — nothing to
install, nothing to bootstrap ([ADR-0011](../../docs/decisions/0011-snowflake-horizon-catalog.md)).

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

`hub-api` and `graphql-authz-proxy` get real charts because nothing upstream
exists for them.

## Prerequisites

- A cluster from [`platform/terraform`](../terraform) with `KUBECONFIG` exported.

### Secrets

`make infra` (the `platform-base` chart) generates every **internal** secret and
keeps it stable across upgrades:

| Secret | Namespace | Holds |
|---|---|---|
| `postgres-secret` | `infra` | postgres + the 4 database passwords |
| `dagster-postgresql-secret` | `data` | mirror of the dagster password |
| `openmetadata-db-auth` | `meta` | mirror of the openmetadata password |
| `openmetadata-fernet-secret` | `meta` | OpenMetadata fernet key |
| `cube-secret` | `data` | hub-api ↔ Cube shared secret |
| `hub-api-db` | `app` | `OHDP_APP_DATABASE_URL`, `OHDP_CUBE_API_SECRET` |
| `oauth2-proxy-secret` | `data` | oauth2-proxy `cookie-secret` (Google `client-id`/`client-secret` merged in externally) |
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
  --from-literal=OHDP_ICEBERG_CREDENTIAL="$(cd ../terraform && terraform output -raw iceberg_credential)"

# OHDP_ICEBERG_CREDENTIAL is the bare Horizon Catalog PAT (not "<user>:<pat>" —
# Snowflake's token endpoint rejects a client_id with invalid_scope), minted by
# Terraform (ADR-0011). The non-secret half — catalog URI, database,
# scope — is in data/ohdp-pipeline-config, so when you `terraform apply` a new
# account or database, update `pipelineConfig` in charts/platform-base/values.yaml
# to match the `iceberg_*` outputs.

kubectl -n app create secret generic hub-api-secrets \
  --from-literal=OHDP_OPENMETADATA_JWT= \
  --from-literal=STRIPE_SECRET_KEY= \
  --from-literal=OIDC_CLIENT_SECRET=

# oauth2-proxy (ADR-0007) — merge the Google client creds into the Secret
# platform-base created (it already holds the generated `cookie-secret`).
# `make base` must have run first. Google Cloud console: a "Web application"
# OAuth client, redirect URI https://dagster.ohdp.kevinmcquate.com/oauth2/callback
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
make repos      # add + update upstream helm repos
make infra      # platform-base, Traefik, cert-manager, ClusterIssuer
# create the external secrets (above)
make install    # opensearch, openmetadata, proxy, dagster
make hub-api    # deployed on its own for now
```

Order matters — OpenSearch must be green before OpenMetadata starts or its
migration job fails; `make install` sequences this for you.

The Iceberg catalog is not installed here: `terraform apply` in
`platform/terraform` creates the Snowflake database, schemas, role, service user
and token, and the pipeline reaches the Horizon endpoint directly. Catalog
inspection is Snowsight — there is no in-cluster admin UI any more (ADR-0011).

`dagster` and `hub-api` run `ghcr.io/kgmcquate/ohdp-{pipeline,hub-api}` at the
`sha-<12>` tag of the current commit — `TAG` defaults to `git rev-parse HEAD`,
which `build-images.yml` pushes on every merge to `main`. Pass `TAG=sha-…` to
pin an older build. Never `latest`: the hub-api chart refuses it, so a pod
restart or `helm rollback` always lands on a known image.

## Before trusting the proxy

`make verify-proxy` sends a mutation and expects a 403. Run it after every
Dagster upgrade — the allowlist is by root field name, and new UI versions
issue new queries.
