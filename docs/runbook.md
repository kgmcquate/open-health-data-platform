# Runbook

Operational procedures. Keep this current — it is the thing you reach for at 2am.

## Build failed — a bad model may already be live

Serving reads the lakehouse through Snowflake, which is also its catalog
(ADR-0019) — there is no publish
gate between a `dbt build` and what dashboards see. dbt materializes each model,
*then* runs its tests, so a test failure means the (possibly bad) data is
already committed by the time you find out. Triage:

1. Open the failed Dagster run. Logs are redacted — if you need raw output, run
   `make dbt-build` locally with lakehouse credentials (`OHDP_SNOWFLAKE_PAT`
   and friends — see `.env.example`).
2. If an upstream API changed shape, fix the ingestion schema contract and the
   staging model in the same PR.
3. If the bad data is already visible downstream, the fix is normally a new
   green `dbt build` run. Unlike the Snowflake-tables era there *is* a rollback
   now: every commit is an Iceberg snapshot, so a table can be rolled back to
   the one before the bad build:

   ```sql
   -- from DuckDB, against the attached catalog
   SELECT * FROM CURATED.CORE.<table>.snapshots();   -- find the good snapshot_id
   ```

   Rolling back is a catalog operation (`rollback_to_snapshot` via pyiceberg);
   prefer re-running the build unless the wrong number is actively on a
   dashboard.

## Postgres restore

_Must be tested before M4 (§11)._

1. Latest dump: `s3://$OHDP_SPACES_BUCKET/backups/pg-*.sql.gz`
2. `gunzip -c pg-*.sql.gz | psql -h $OHDP_POSTGRES_HOST -U postgres`
3. Restart consumers: Dagster, OpenMetadata, hub-api.

## Node memory pressure

Symptom: pods OOMKilled. *Serving* queries run in Snowflake (ADR-0019), not on
this node — but since that ADR the **transform** compute is DuckDB, in the
pipeline pod, so a `dbt build` is now the most likely cause rather than just a
large dlt load. Check the pipeline pod's memory first and raise its limit
(`platform/helm/values/dagster.yaml`) if a model is spilling. If a single
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
