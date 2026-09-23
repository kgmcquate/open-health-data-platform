# Open Health Data Platform

A self-hosted analytics platform over **public** health data (OpenAQ, CDC, openFDA,
CMS, WHO GHO). Batch-materialized once, served read-only to dashboards, a chatbot,
and alerting through a single semantic layer. Free and paid ($5/mo) tiers.

Every component is intentionally user-visible — this is a portfolio project as much
as a product.

> **Not clinical decision support.** All figures are population-level and
> non-clinical. Generated insights carry disclaimers. See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) §10.

## Repository layout

| Path | What lives here |
|---|---|
| `apps/web` | Vite/React hub — landing, topics, chat UI, links out to every tool |
| `apps/api` | FastAPI — chat orchestration, entitlements, Stripe webhooks |
| `data/src/ohdp_ingestion` | One typed client per public source |
| `data/src/ohdp_orchestration` | Dagster: ingestion, dbt build, catalog sync, alerts |
| `data/dbt` | SQL transformation + tests, dbt-duckdb writing Iceberg (source of truth for models) |
| `data/src/ohdp_ml` | Anomaly detection and forecasting, classical baselines first |
| `semantic/cube` | Cube Core semantic layer — one definition per metric |
| `catalog/openmetadata` | Catalog sync (Cube → metrics, dbt → lineage) and seed data |
| `platform/terraform` | DigitalOcean cluster, DNS + Spaces, Snowflake (the Iceberg catalog) + its S3 volume |
| `platform/helm` | Charts for hub-api and the authz proxy; values for upstream charts |
| `platform/k3s` | Base manifests — namespaces, ingress, cert-manager |
| `packages/shared` | Shared Python config, types, redacting logger |
| `docs` | Architecture, ADRs, runbook |

## Getting started

```bash
uv sync                 # Python workspace (data, api, packages)
cp .env.example .env     # fill in secrets locally; never commit
```

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full design,
[`docs/decisions/`](docs/decisions/) for ADRs, and
[`docs/deploying.md`](docs/deploying.md) for the deploy workflows (all runnable
locally with `act`).

```bash
make act-preflight   # validate charts, policy and manifests — no cluster needed
make images          # build all container images with docker
```

## Build order

`M0` pipeline spine → `M1` k3s platform → `M2` semantic layer + catalog →
`M3` chatbot → `M4` monetization → `M5` ML. Each milestone is independently
demoable; details in the architecture doc §8.

## License

[PolyForm Noncommercial 1.0.0](LICENSE.md) — source is visible and free to
read, self-host, and modify for noncommercial purposes (including by
nonprofits, schools, research, and public health/government bodies).
Commercial use requires a separate license from the copyright holder.
