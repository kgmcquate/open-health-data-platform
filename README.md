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
| `apps/web` | Next.js hub — landing, chat UI, embedded dashboards |
| `apps/api` | FastAPI — chat orchestration, entitlements, Stripe webhooks |
| `data/ingestion` | One typed client per public source |
| `data/dagster` | Orchestration: ingestion, dbt build, snapshot publish, alerts |
| `data/dbt` | SQL transformation + tests against DuckDB (source of truth for models) |
| `data/ml` | Anomaly detection and forecasting, classical baselines first |
| `semantic/cube` | Cube Core semantic layer — one definition per metric |
| `catalog/openmetadata` | Catalog sync (Cube → metrics, dbt → lineage) and seed data |
| `platform/k3s` | k3s manifests and Helm values for the single VM |
| `platform/graphql-proxy` | Dagster GraphQL allowlist proxy (read-only enforcement) |
| `packages/shared` | Shared Python config, types, redacting logger |
| `docs` | Architecture, ADRs, runbook |

## Getting started

```bash
uv sync                 # Python workspace (data, api, packages)
cp .env.example .env     # fill in secrets locally; never commit
```

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full design and
[`docs/decisions/`](docs/decisions/) for ADRs.

## Build order

`M0` pipeline spine → `M1` k3s platform → `M2` semantic layer + catalog →
`M3` chatbot → `M4` monetization → `M5` ML. Each milestone is independently
demoable; details in the architecture doc §8.

## License

Not yet chosen. Do not assume a license until one is added here.
