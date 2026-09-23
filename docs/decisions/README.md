# Architecture Decision Records

One file per decision, numbered, immutable once accepted. To reverse a decision,
add a new ADR that supersedes the old one — don't edit history.

Format: Context → Decision → Consequences. Keep them to a page.

| # | Title | Status |
|---|---|---|
| [0001](0001-single-vm-k3s.md) | Single VM with k3s, not managed Kubernetes | Accepted |
| [0002](0002-publish-and-replicate-duckdb.md) | Publish-and-replicate for DuckDB serving | Accepted |
| [0003](0003-cube-core-over-dbt-semantic-layer.md) | Cube Core as the semantic layer | Accepted; "no Cube Store initially" consequence superseded by [0024](0024-cube-store-pre-aggregations.md) |
| [0004](0004-orchestration-package-import-name.md) | Orchestration package imported as `ohdp_orchestration` | Accepted |
| [0005](0005-hetzner-k3s-over-managed-kubernetes.md) | Hetzner + k3s over managed Kubernetes | Accepted |
| [0006](0006-upstream-charts-and-external-authz-proxy.md) | Upstream Helm charts, external authz proxy | Accepted |
| [0007](0007-oauth2-proxy-google-sso.md) | Google login wall in front of Dagster (oauth2-proxy) | Accepted |
| [0008](0008-config-driven-healthdata-gov-ingestion.md) | Config-driven HealthData.gov ingestion (Dagster component + dlt) | Accepted |
| [0010](0010-iceberg-medallion-lakehouse.md) | Iceberg medallion lakehouse, Polaris catalog | Superseded by [0012](0012-native-snowflake-tables.md); shape revived by [0019](0019-iceberg-on-s3-duckdb-dbt.md) |
| [0011](0011-snowflake-horizon-catalog.md) | Snowflake Horizon Catalog replaces Polaris; Terraform-managed | Superseded by [0012](0012-native-snowflake-tables.md) |
| [0012](0012-native-snowflake-tables.md) | Native Snowflake tables replace the Iceberg lake | Superseded by [0019](0019-iceberg-on-s3-duckdb-dbt.md) |
| [0013](0013-per-layer-snowflake-databases.md) | One Snowflake database per medallion layer (RAW/CLEAN/CURATED) | Accepted |
| [0014](0014-snowflake-only-compilation.md) | Snowflake-only: drop the local/CI DuckDB targets | Superseded by [0019](0019-iceberg-on-s3-duckdb-dbt.md) |
| [0015](0015-streamlit-over-superset.md) | Streamlit over Superset for dashboards | Superseded — Streamlit removed; dashboards-as-code in chat per [0025](0025-dashboards-as-code-in-chat.md) |
| [0016](0016-chat-agent-tool-surface.md) | Chat agent tool surface: own Cube tools, OpenMetadata MCP for context | Accepted; tool split amended by [0025](0025-dashboards-as-code-in-chat.md); write-back consequence amended by [0027](0027-chat-agent-memory-write-back.md) |
| [0017](0017-open-webui-chat-ui.md) | Open WebUI as a chat UI, with our own Cube MCP server | Superseded / removed — Hub UI is the single chat surface |
| [0018](0018-socrata-ingestion-shared-across-domains.md) | One Socrata ingestion core, bound per domain (CDC) | Accepted |
| [0019](0019-iceberg-on-s3-duckdb-dbt.md) | Snowflake as the Iceberg catalog; DuckDB builds, Snowflake serves | Accepted |
| [0020](0020-pipeline-creates-its-own-namespaces.md) | dbt and dlt create the catalog namespaces, not Terraform | Superseded by [0021](0021-terraform-creates-the-namespaces-again.md) |
| [0021](0021-terraform-creates-the-namespaces-again.md) | Terraform creates the catalog namespaces again | Accepted |
| [0022](0022-token-usage-limits.md) | Per-user/group token usage limits on Open WebUI | Superseded — Open WebUI removed; hub-api quota gate remains |
| [0023](0023-openrouter-glm-model-backend.md) | OpenRouter + GLM 5.3 Flash as Open WebUI's model backend | Superseded — Open WebUI removed |
| [0024](0024-cube-store-pre-aggregations.md) | Cube Store and pre-aggregations | Accepted |
| [0025](0025-dashboards-as-code-in-chat.md) | Dashboards as code, rendered in the chat turn | Accepted; saving/publishing amended by [0028](0028-agent-published-dashboards-ranked-by-votes.md) |
| [0026](0026-cms-rest-api-ingestion.md) | CMS ingestion over its own REST API, not Socrata; Iceberg landing side shared | Accepted |
| [0027](0027-chat-agent-memory-write-back.md) | Chat agent writes OpenMetadata memories unreviewed | Accepted; amends [0016](0016-chat-agent-tool-surface.md) |
| [0028](0028-agent-published-dashboards-ranked-by-votes.md) | The chat agent publishes dashboards; readers vote them down | Accepted; amends [0025](0025-dashboards-as-code-in-chat.md) |
