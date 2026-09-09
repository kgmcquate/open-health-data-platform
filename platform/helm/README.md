# Helm

Charts we own, and values files for the upstream charts.

```
charts/
  platform-base/           our chart — namespaces, generated internal secrets,
                           the shared Postgres StatefulSet, the ClusterIssuer
  external-secrets-config/  our chart — the Doppler ClusterSecretStore + ExternalSecrets
  hub-api/                 our chart — the FastAPI backend
  graphql-authz-proxy/     our chart — wraps kgmcquate/graphql-authz-proxy
values/
  traefik.yaml             for traefik/traefik
  cert-manager.yaml        for jetstack/cert-manager
  external-secrets.yaml    for external-secrets/external-secrets
  dagster.yaml             for dagster/dagster
  openmetadata.yaml        for open-metadata/openmetadata  (pinned to 1.13.x)
  opensearch.yaml          for opensearch/opensearch
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
- A Doppler service token in the cluster — the one secret you create by hand:

  ```bash
  kubectl create namespace external-secrets
  kubectl -n external-secrets create secret generic doppler-token \
    --from-literal=dopplerToken=dp.st.xxxxxxxx
  ```

  It must be scoped to a Doppler config holding: `STRIPE_SECRET_KEY`,
  `OIDC_CLIENT_SECRET`, `OHDP_OPENMETADATA_JWT`, `OHDP_R2_ACCESS_KEY_ID`,
  `OHDP_R2_SECRET_ACCESS_KEY`, `OHDP_R2_ENDPOINT_URL`, `OHDP_OPENAQ_API_KEY`,
  `OHDP_CDC_APP_TOKEN`. Keys map 1:1 in `charts/external-secrets-config/values.yaml`.

Everything else is created by `make infra`:

| What | Created by | Secret / resource |
|---|---|---|
| Namespaces `app data bi meta infra` | `platform-base` | — |
| Postgres passwords (× 5), Cube secret, OM fernet key | `platform-base` (generated once, kept on upgrade) | `infra/postgres-secret`, `data/dagster-postgresql-secret`, `meta/openmetadata-db-auth`, `meta/openmetadata-fernet-secret`, `data/cube-secret`, `app/hub-api-db` |
| Non-secret pipeline env | `platform-base` | `data/ohdp-pipeline-config` (ConfigMap) |
| Shared Postgres StatefulSet | `platform-base` | `infra` |
| `letsencrypt-prod` ClusterIssuer | `platform-base` (once cert-manager CRDs exist) | — |
| Stripe / OIDC / R2 / API keys | External Secrets Operator ← Doppler | `data/ohdp-pipeline-secrets`, `app/hub-api-secrets` |

## Install

```bash
make repos      # add + update upstream helm repos
make infra      # platform-base, Traefik, cert-manager, ClusterIssuer, ESO
make install    # opensearch, openmetadata, proxy
make dagster TAG=sha-...     # needs a built pipeline image
make hub-api  TAG=sha-...
```

Order matters — OpenSearch must be green before OpenMetadata starts or its
migration job fails; `make install` and `make infra` sequence this for you.

## Before trusting the proxy

`make verify-proxy` sends a mutation and expects a 403. Run it after every
Dagster upgrade — the allowlist is by root field name, and new UI versions
issue new queries.
