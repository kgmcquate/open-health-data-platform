# Architecture Decision Records

One file per decision, numbered, immutable once accepted. To reverse a decision,
add a new ADR that supersedes the old one — don't edit history.

Format: Context → Decision → Consequences. Keep them to a page.

| # | Title | Status |
|---|---|---|
| [0001](0001-single-vm-k3s.md) | Single VM with k3s, not managed Kubernetes | Accepted |
| [0002](0002-publish-and-replicate-duckdb.md) | Publish-and-replicate for DuckDB serving | Accepted |
| [0003](0003-cube-core-over-dbt-semantic-layer.md) | Cube Core as the semantic layer | Accepted |
| [0004](0004-orchestration-package-import-name.md) | Orchestration package imported as `ohdp_orchestration` | Accepted |
| [0005](0005-hetzner-k3s-over-managed-kubernetes.md) | Hetzner + k3s over managed Kubernetes | Accepted |
| [0006](0006-upstream-charts-and-external-authz-proxy.md) | Upstream Helm charts, external authz proxy | Accepted |
| [0007](0007-oauth2-proxy-google-sso.md) | Google login wall in front of Dagster (oauth2-proxy) | Accepted |
| [0008](0008-config-driven-healthdata-gov-ingestion.md) | Config-driven HealthData.gov ingestion (Dagster component + dlt) | Accepted |
| [0010](0010-iceberg-medallion-lakehouse.md) | Iceberg medallion lakehouse, Polaris catalog | Superseded by [0012](0012-native-snowflake-tables.md) |
| [0011](0011-snowflake-horizon-catalog.md) | Snowflake Horizon Catalog replaces Polaris; Terraform-managed | Superseded by [0012](0012-native-snowflake-tables.md) |
| [0012](0012-native-snowflake-tables.md) | Native Snowflake tables replace the Iceberg lake | Accepted |
| [0013](0013-per-layer-snowflake-databases.md) | One Snowflake database per medallion layer (RAW/CLEAN/CURATED) | Accepted |
