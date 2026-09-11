# Helm

Charts we own, and values files for the upstream charts.

```
charts/
  platform-base/        our chart — namespaces, generated internal secrets,
                        the shared Postgres StatefulSet, the ClusterIssuer
  hub-api/              our chart — the FastAPI backend
  graphql-authz-proxy/  our chart — wraps kgmcquate/graphql-authz-proxy
vendor/
  polaris-console/      copied verbatim from apache/polaris-tools (no Helm repo
                        upstream). See its VENDORED.md; image built by
                        .github/workflows/build-polaris-console.yml
values/
  traefik.yaml          for traefik/traefik
  cert-manager.yaml     for jetstack/cert-manager
  dagster.yaml          for dagster/dagster
  openmetadata.yaml     for open-metadata/openmetadata  (pinned to 2.0.x)
  opensearch.yaml       for opensearch/opensearch
  oauth2-proxy.yaml     for oauth2-proxy/oauth2-proxy  (Google wall → Dagster, ADR-0007)
  oauth2-proxy-polaris.yaml  for oauth2-proxy/oauth2-proxy  (Google wall → Polaris console, ADR-0010)
  polaris.yaml          for polaris/polaris  (Iceberg REST catalog, ADR-0010)
  polaris-console.yaml  for vendor/polaris-console
```

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
| `polaris-persistence` | `data` | Polaris metastore `jdbcUrl` / `username` / `password` (derived from `postgres-secret`) |
| `polaris-principal` | `data` | `OHDP_ICEBERG_CREDENTIAL` — the generated `ohdp` realm root principal (`<id>:<secret>`); loaded by the pipeline |
| `polaris-bootstrap` | `data` | `credential` — same principal, `<realm>,<id>,<secret>` form, for the chart's schema-bootstrap init container |
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
  --from-literal=OHDP_OPENMETADATA_JWT=          # fill after OpenMetadata's first boot

# Polaris (ADR-0010) needs no external secret of its own — platform-base
# generates the metastore connection and the `ohdp` realm root principal
# (polaris-persistence / polaris-principal / polaris-bootstrap). It does read the
# Spaces keys from `ohdp-pipeline-secrets` above (values/polaris.yaml
# `storage.secret`): a non-staged REST `create table` has the server write the
# first metadata.json, so Polaris needs its own object-store creds. `make polaris`
# installs polaris/polaris (its init container creates the schema + `ohdp`
# realm) and then creates the `ohdp` catalog via the management API. See
# platform/scripts/polaris-bootstrap.sh.

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
make install    # opensearch, openmetadata, polaris(+console+wall), proxy, dagster
make hub-api    # deployed on its own for now
```

Order matters — OpenSearch must be green before OpenMetadata starts or its
migration job fails; `make install` sequences this for you. Polaris bootstraps
its own schema + `ohdp` realm from an init container; its post-install catalog
step reads the principal from the `polaris-principal` secret. Every Polaris
secret is generated by `make base` — nothing to create by hand.

The Polaris console UI is at `https://polaris.ohdp.kevinmcquate.com`, behind its
own Google login wall (`oauth2-proxy-polaris`, an email allowlist — not the
any-Google-account Dagster wall) which also reverse-proxies `/api/*` on that host
to the Polaris service, so the SPA and the catalog API share one origin. Log in
on the console with the `ohdp` root principal's `clientId` / `clientSecret` from
`polaris-principal` (Client Credentials — Polaris itself still runs internal auth;
see ADR-0010 for why OIDC needs more).

Two out-of-band prerequisites:

- **DNS**: `polaris.ohdp.kevinmcquate.com` → the Traefik LB (Cloudflare), like
  `dagster.ohdp`.
- **Google OAuth client**: add `https://polaris.ohdp.kevinmcquate.com/oauth2/callback`
  to the authorized redirect URIs of the same client used for Dagster
  (`DAGSTER_OIDC_CLIENT_ID`) — `oauth2-proxy-polaris` reuses `oauth2-proxy-secret`.

The console image is not on the `main` build path: run **Build Polaris console
image** (or bump `POLARIS_TOOLS_REF` in it), keep `image.tag` in
`values/polaris-console.yaml` and the pin in `vendor/polaris-console/VENDORED.md`
in sync, and re-vendor the chart with `make vendor-polaris-console REF=<sha>`.

`dagster` and `hub-api` run `ghcr.io/kgmcquate/ohdp-{pipeline,hub-api}` at the
`sha-<12>` tag of the current commit — `TAG` defaults to `git rev-parse HEAD`,
which `build-images.yml` pushes on every merge to `main`. Pass `TAG=sha-…` to
pin an older build. Never `latest`: the hub-api chart refuses it, so a pod
restart or `helm rollback` always lands on a known image.

## Before trusting the proxy

`make verify-proxy` sends a mutation and expects a 403. Run it after every
Dagster upgrade — the allowlist is by root field name, and new UI versions
issue new queries.
