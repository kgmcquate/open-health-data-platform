# 0024 — Cube Store and pre-aggregations

**Status:** Accepted
**Supersedes:** ADR-0003's "no Cube Store and no pre-aggregations initially"
consequence. The rest of ADR-0003 (Cube Core as the single semantic layer,
every cube reads a mart, `queryRewrite` as the safety control) is unchanged.

## Context

ADR-0003 shipped Cube with `CUBEJS_CACHE_AND_QUEUE_DRIVER: memory` and every
query going straight to Snowflake, deliberately deferring Cube Store: "add
later only if query latency demands it." That latency has now shown up on
the chatbot path specifically — the chat agent builds ad hoc queries per tool
chat agent builds ad hoc queries per tool call, each one a cold hit against
Snowflake (warehouse spin-up plus network round trip), unlike Streamlit's
mostly-repeated dashboard queries.

Every cube in `semantic/cube/model/` is small, low-cardinality public-health
surveillance data (state/week grain, a handful of dimensions) — there is no
data-volume reason to be selective about what gets cached.

## Decision

Deploy Cube Store as a standalone single-process instance (`platform/helm/
charts/cubestore` — not the router/worker split, which is for horizontal
scale this workload doesn't need). Point `cube` at it
(`CUBEJS_CACHE_AND_QUEUE_DRIVER: cubestore`) and turn on
`scheduledRefreshTimer` (hourly) in `semantic/cube/cube.js`.

Every cube gets a `type: originalSql` pre-aggregation rather than
hand-enumerated `rollup` measures/dimensions: it copies the cube's full mart
into Cube Store as-is, and Cube re-aggregates from that local copy at query
time for any query shape — including the unpredictable ones the chat agent
builds — without having to predict which measure/dimension combinations
it'll ask for. Each inherits the cube's existing `refresh_key`
(`MAX("ingest_ts")`) unchanged.

## Consequences

- New stateful pod + PVC (ARCHITECTURE.md §4: `cubestore`, 20Gi
  `do-block-storage`, ~0.25–0.5 GB). `cube` now depends on it at startup —
  deploy `cubestore` first (`platform/helm/Makefile`, `deploy-platform.yml`).
- Metric changes still touch the same files ADR-0003 named (dbt model, Cube
  model, OpenMetadata sync); adding a new measure to an existing cube needs
  no pre-aggregation change, since the copy is of the whole mart already.
- `queryRewrite`'s row caps and statement timeouts still apply — Cube Store
  changes where a query runs, not what's allowed to run.
- If a cube's mart grows past "small," `originalSql` stops being the right
  default for it and it should move to hand-enumerated `rollup`
  measures/dimensions instead — not a reason to revisit this ADR wholesale.
