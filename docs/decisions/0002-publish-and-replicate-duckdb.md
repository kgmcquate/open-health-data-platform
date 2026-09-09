# 0002 — Publish-and-replicate for DuckDB serving

**Status:** Accepted

## Context

DuckDB allows multiple read-only processes to open a file **only when no process
holds it read-write**. The dbt build writes; dashboards, Cube, and alerting read.
They cannot share one file. DuckDB's client-server ("Quack") protocol that would
remove this constraint is not stable until DuckDB 2.0, which had no RC date as of
August 2026 (ARCHITECTURE.md §9).

## Decision

The build writes a local DuckDB file, runs dbt tests, and — only on green —
uploads an immutable `warehouse-{ts}.duckdb` to Cloudflare R2 and updates a
`current.json` pointer. The read replica's init container pulls the current
snapshot onto local NVMe and opens it read-only. Publishing a new snapshot
triggers a rolling restart of the replica.

## Consequences

- **Rollback is a pointer change.** Snapshots are immutable, retained for N
  versions (provisional: 14 daily, §10.4).
- The dbt test gate is the only thing between a broken upstream API and a wrong
  number on a dashboard. Never publish on failed tests (§3).
- Serving reads are always seconds-to-minutes stale relative to the build. That
  is acceptable for this data.
- Re-evaluate when DuckDB 2.0 / Quack ships; document the evaluation, keep this
  path until it clearly wins (§9).
