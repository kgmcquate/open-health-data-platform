# Runbook

Operational procedures. Keep this current — it is the thing you reach for at 2am.

## Snapshot rollback

The serving warehouse is a pointer. To roll back:

1. List snapshots: `aws s3 ls s3://$OHDP_R2_BUCKET/snapshots/ --endpoint-url $OHDP_R2_ENDPOINT_URL`
2. Set `current.json` to the last-known-good `warehouse-{ts}.duckdb`.
3. Roll the replica: `kubectl -n data rollout restart deploy/duckdb-replica`
4. Confirm freshness in Superset and via `/healthz` on Cube.

_Snapshots are immutable and retained for 14 versions (provisional, ADR pending §10.4)._

## Build failed — no snapshot published

Expected behaviour when a dbt test fails (ARCHITECTURE.md §3). Serving keeps the
previous snapshot. Triage:

1. Open the failed Dagster run. Logs are redacted — if you need raw output, run
   `dbt build` locally against a fresh DuckDB.
2. If an upstream API changed shape, fix the ingestion schema contract and the
   staging model in the same PR.
3. Do not manually publish a snapshot to unblock. Fix forward.

## Postgres restore

_Must be tested before M4 (§11)._

1. Latest dump: `s3://$OHDP_R2_BUCKET/backups/pg-*.sql.gz`
2. `gunzip -c pg-*.sql.gz | psql -h $OHDP_POSTGRES_HOST -U postgres`
3. Restart consumers: Dagster, Superset, OpenMetadata, hub-api.

## Node memory pressure

Symptom: pods OOMKilled, DuckDB queries failing. Check the serving worker pool
metrics (queue wait, peak memory, spill bytes — §6). If a single query pattern is
the cause, tighten the Cube `queryRewrite` caps. The node has no headroom for an
unbounded scan by design.

## Dagster GraphQL proxy

If the Dagster UI shows errors after a Dagster upgrade, a query may use a new root
field not on the allowlist. Add it to `ALLOWED_ROOT_FIELDS` in
`platform/graphql-proxy/proxy/main.py` **only after** confirming it is read-only.
Never switch the proxy to a denylist.
