# Runbook

Operational procedures. Keep this current — it is the thing you reach for at 2am.

## Build failed — a bad model may already be live

Serving reads Snowflake directly (ADR-0012) — there is no publish gate between a
`dbt build` and what dashboards see. dbt materializes each `table`/`incremental`
model, *then* runs its tests, so a test failure means the (possibly bad) data
is already in the table by the time you find out. Triage:

1. Open the failed Dagster run. Logs are redacted — if you need raw output, run
   `dbt build` locally against a fresh DuckDB (`--target local`/`--target ci`)
   or, with credentials, `--target prod`.
2. If an upstream API changed shape, fix the ingestion schema contract and the
   staging model in the same PR.
3. If the bad data is already visible downstream, the fix is a new green
   `dbt build` run, not a rollback — there is no prior-snapshot pointer to
   revert to. Time Travel (`snowflake_data_retention_days`) can recover a
   table's pre-run state by hand if it's urgent:
   `CREATE OR REPLACE TABLE <schema>.<table> AS SELECT * FROM <schema>.<table> AT (OFFSET => -3600);`

## Postgres restore

_Must be tested before M4 (§11)._

1. Latest dump: `s3://$OHDP_SPACES_BUCKET/backups/pg-*.sql.gz`
2. `gunzip -c pg-*.sql.gz | psql -h $OHDP_POSTGRES_HOST -U postgres`
3. Restart consumers: Dagster, Superset, OpenMetadata, hub-api.

## Node memory pressure

Symptom: pods OOMKilled. Serving queries run in Snowflake now (ADR-0012), not on
this node, so this is almost always the pipeline pod during a `dbt build` or a
large dlt load — check its memory, not a serving worker pool. If a single
dashboard query pattern is the cause instead, tighten the Cube `queryRewrite`
caps; Cube itself still runs on this node, Snowflake compute does not.

## Dagster GraphQL proxy

If the Dagster UI shows errors after a Dagster upgrade, a query may use a new root
field not on the allowlist. The proxy logs the rejected field name:

```bash
kubectl -n data logs deploy/gqlproxy-graphql-authz-proxy | grep denied
```

Confirm the field is read-only, then add it under
`authz.groups[public-viewer].permissions.queries.fields` in
`platform/helm/charts/graphql-authz-proxy/values.yaml` and redeploy. Never widen
to `field_name: "*"`, and never switch to a denylist — a denylist silently
reopens on every Dagster upgrade.

Then re-assert the control still holds:

```bash
cd platform/helm && make verify-proxy
```

The config is read once at pod start. The chart puts a checksum annotation on the
pod template so a values change rolls it; if you edit the ConfigMap by hand
instead, restart the Deployment or the change does nothing.
