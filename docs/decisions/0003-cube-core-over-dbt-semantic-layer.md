# 0003 — Cube Core as the semantic layer

**Status:** Accepted

## Context

Three consumers (dashboards, chatbot, alerting) must agree on every metric
definition (ARCHITECTURE.md §1.5). Options considered:

- **dbt Semantic Layer / MetricFlow serving APIs** — require dbt Cloud at
  ~$100/user/month. Out of budget (§9). MetricFlow itself is OSS; the serving
  tier is not.
- **Metrics defined ad hoc in Superset** — no shared definition; the chatbot and
  alerting would re-implement them and drift.
- **Cube Core (OSS)** — self-hostable in ~0.5 GB, exposes a REST/SQL/MCP
  interface, has `queryRewrite` for per-tier limits, and gives the chat agent a
  bounded measure/dimension surface instead of raw SQL (§6).

## Decision

Cube Core is the single semantic layer. Superset queries Cube, not DuckDB. The
chat agent reaches data only through Cube's MCP endpoint. Every cube reads a dbt
**mart**, never a staging or raw table.

## Consequences

- No Cube Store and no pre-aggregations initially (§4 budget). Add later only if
  query latency demands it.
- `queryRewrite` is a load-bearing safety control — hard row caps and statement
  timeouts apply there to every query including agent-built ones.
- Metric changes now touch three files in one PR (dbt model, Cube model,
  OpenMetadata sync). That coupling is the reason for the monorepo (§7).
