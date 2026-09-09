# platform

Everything that turns one Hetzner CX53 into the running system (ARCHITECTURE.md §4).

- `k3s/base/` — namespaces (`app`, `data`, `bi`, `meta`, `infra`), Traefik
  ingress, cert-manager, ClusterIssuer.
- `k3s/charts/` — Helm values per component (Dagster, Superset, OpenMetadata,
  OpenSearch, Postgres, Cube, oauth2-proxy).
- `k3s/overlays/` — per-environment patches.
- `graphql-proxy/` — the Dagster GraphQL allowlist proxy. **Allowlist, not
  denylist** (§5). This is what actually enforces read-only, not Dagster's UI mode.
- `scripts/` — bootstrap, `pg_dump` backup to R2, restore (tested before M4, §11).

## Non-negotiables

- Memory **limits** on every pod (§4). An unbounded DuckDB query otherwise takes
  the node down.
- No managed Kubernetes. No second always-on database. No HA / autoscaling (§9).
- Nothing in the Dagster UI you would not put on a public webpage (§9).
