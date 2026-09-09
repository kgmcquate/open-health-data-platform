# Architecture Decision Records

One file per decision, numbered, immutable once accepted. To reverse a decision,
add a new ADR that supersedes the old one — don't edit history.

Format: Context → Decision → Consequences. Keep them to a page.

| # | Title | Status |
|---|---|---|
| [0001](0001-single-vm-k3s.md) | Single VM with k3s, not managed Kubernetes | Accepted |
| [0002](0002-publish-and-replicate-duckdb.md) | Publish-and-replicate for DuckDB serving | Accepted |
| [0003](0003-cube-core-over-dbt-semantic-layer.md) | Cube Core as the semantic layer | Accepted |
| [0004](0004-orchestration-package-import-name.md) | Remap the `data/dagster` import name | Accepted |
| [0005](0005-hetzner-k3s-over-managed-kubernetes.md) | Hetzner + k3s over managed Kubernetes | Accepted |
| [0006](0006-upstream-charts-and-external-authz-proxy.md) | Upstream Helm charts, external authz proxy | Accepted |
