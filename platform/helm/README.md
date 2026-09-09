# Helm

Two charts we own, and values files for three upstream charts.

```
charts/
  hub-api/              our chart — the FastAPI backend
  graphql-authz-proxy/  our chart — wraps kgmcquate/graphql-authz-proxy
values/
  dagster.yaml          for dagster/dagster
  openmetadata.yaml     for open-metadata/openmetadata
  opensearch.yaml       for opensearch/opensearch
```

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
- Namespaces + cert-manager + ClusterIssuer from [`platform/k3s/base`](../k3s/base).
- **Postgres in `infra`**, with databases `dagster`, `superset`, `openmetadata`,
  `app` (§2 — one instance, four databases; §9 — do not split it).
  Not yet written. When you do it in M1, prefer a plain StatefulSet or
  CloudNativePG over the Bitnami chart — Bitnami moved most of its free image
  catalog behind Bitnami Secure Images in 2025, so `bitnami/postgresql` is no
  longer the safe default it used to be.
- Secrets created out of band. None of them are in git:

  | Secret | Namespace | Keys |
  |---|---|---|
  | `dagster-postgresql-secret` | `data` | `postgresql-password` |
  | `ohdp-pipeline-secrets` | `data` | R2 creds, source API keys, OM JWT |
  | `ohdp-pipeline-config` (ConfigMap) | `data` | non-secret pipeline env |
  | `hub-api-secrets` | `app` | DB URL, Cube secret, Stripe, OIDC |
  | `openmetadata-db-secret` | `meta` | `openmetadata-postgres-password` |
  | `openmetadata-fernet-secret` | `meta` | `fernetKey` |

## Install

```bash
make repos      # add + update upstream helm repos
make install    # everything, in dependency order
```

Or one at a time — order matters, OpenSearch must be green before OpenMetadata
starts or its migration job fails:

```bash
make opensearch && make openmetadata
make dagster
make proxy
make hub-api
```

## Before trusting the proxy

`make verify-proxy` sends a mutation and expects a 403. Run it after every
Dagster upgrade — the allowlist is by root field name, and new UI versions
issue new queries.
