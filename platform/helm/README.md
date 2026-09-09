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
  openmetadata.yaml     for open-metadata/openmetadata  (pinned to 1.13.x)
  opensearch.yaml       for opensearch/opensearch
```

Every upstream chart version is pinned in the `Makefile` (`*_VERSION`).
`helm upgrade --install` otherwise pulls the newest — and OpenMetadata 2.0.x
restructured its secret/config layout in a way these values do not support.

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
| `ohdp-pipeline-config` (ConfigMap) | `data` | non-secret pipeline env |

The **external** secrets are GitHub Actions repo secrets, injected by the
`secrets` step of [`deploy-platform.yml`](../../.github/workflows/deploy-platform.yml).
For a manual deploy, create them yourself after `make base`:

```bash
kubectl -n data create secret generic ohdp-pipeline-secrets \
  --from-literal=OHDP_R2_ACCESS_KEY_ID=... \
  --from-literal=OHDP_R2_SECRET_ACCESS_KEY=... \
  --from-literal=OHDP_R2_ENDPOINT_URL=https://nyc3.digitaloceanspaces.com \
  --from-literal=OHDP_OPENAQ_API_KEY=... \
  --from-literal=OHDP_CDC_APP_TOKEN=... \
  --from-literal=OHDP_OPENMETADATA_JWT=          # fill after OpenMetadata's first boot

kubectl -n app create secret generic hub-api-secrets \
  --from-literal=OHDP_OPENMETADATA_JWT= \
  --from-literal=STRIPE_SECRET_KEY= \
  --from-literal=OIDC_CLIENT_SECRET=
```

`OHDP_OPENMETADATA_JWT` is minted by OpenMetadata itself (Settings → Bots →
`ingestion-bot`); leave it empty for the first deploy, then patch both secrets
and restart the consumers.

## Install

```bash
make repos      # add + update upstream helm repos
make infra      # platform-base, Traefik, cert-manager, ClusterIssuer
# create the external secrets (above)
make install    # opensearch, openmetadata, proxy
make dagster TAG=sha-...     # needs a built pipeline image
make hub-api  TAG=sha-...
```

Order matters — OpenSearch must be green before OpenMetadata starts or its
migration job fails; `make install` sequences this for you.

## Before trusting the proxy

`make verify-proxy` sends a mutation and expects a 403. Run it after every
Dagster upgrade — the allowlist is by root field name, and new UI versions
issue new queries.
