# 0029 — MotherDuck serves Cube; Snowflake stays the Iceberg catalog

**Status:** Accepted
**Amends:** ADR-0019's "DuckDB builds, Snowflake serves". Snowflake remains
the Iceberg catalog (Horizon) for every writer and reader; only the compute
behind Cube moves.

## Context

Since ADR-0019, Cube has been the only thing that runs Snowflake compute:
the `OHDP_WH` warehouse exists solely for Cube's queries and for Cube Store's
hourly pre-aggregation refreshes (ADR-0024). The pipeline already reads and
writes the same tables from DuckDB through Horizon's Iceberg REST catalog.

MotherDuck can now (preview, July 2026) attach an external Iceberg REST
catalog *server-side*, as a persistent database in the workspace, and query
it on MotherDuck's compute. Cube Core ships a DuckDB driver that connects to
MotherDuck with a service token.

## Decision

Cube's data source becomes MotherDuck (`CUBEJS_DB_TYPE: duckdb` +
`CUBEJS_DB_DUCKDB_MOTHERDUCK_TOKEN`). Horizon's `CURATED` catalog is attached
to MotherDuck as a read-only Iceberg database also named `CURATED`, so the
`"CURATED"."<SCHEMA>"."<TABLE>"` relation names in the dbt manifest — and
therefore every cube model — resolve unchanged.

`platform/scripts/motherduck_bootstrap.py` owns that attachment. It stores the
pipeline's Horizon PAT in MotherDuck as an `ICEBERG` secret (`IN MOTHERDUCK`),
with the same empty-client-id + `session:role:<ROLE>` scope the dbt profile
uses, and creates the database if it's missing. Run it once for a new
workspace.

**Amended 2026-09-26:** the attach turned out to hold the OAuth access token
the secret had when the database was created, not the PAT, and Horizon
issues those tokens for one hour. Nothing refreshes them, so an hour after the
attach every CURATED read fails with a 401. Replacing the secret doesn't fix
an existing attach, and an open MotherDuck session keeps the attach it
connected with. So Cube re-creates the secret and `CURATED` itself.
`MotherDuckSessionDriver` in `semantic/cube/cube.js` opens a new session
every 45 minutes and runs the re-create before that session serves any query.
That also covers PAT rotation.

ADR-0024's `originalSql` pre-aggregations are persisted in the source
database, not Cube Store (Cube's documented behaviour for that type). On
Snowflake that meant native tables in `CURATED.PROD_PRE_AGGREGATIONS`; on
MotherDuck they land in a native MotherDuck database, `cache`, as
`cache.prod_pre_aggregations`: Cube connects to `md:cache`, making it the
default database its unqualified pre-aggregation tables resolve to (`CURATED`
is attached read-only and couldn't hold them anyway). The Snowflake copies
are orphaned by this change and can be dropped.

## Consequences

- The `cube-snowflake` Secret is replaced by `cube-motherduck`. Cube still
  reads the Horizon PAT (from `ohdp-pipeline-secrets`) to re-create the attach
  (amendment above). The PAT also has a copy in MotherDuck.
- Queries still running on the old session while a new one re-creates
  `CURATED`, about every 45 minutes, can fail.
- `OHDP_WH` and the RSA key pair are now unused by anything we run. They stay
  in Terraform for a cheap rollback (revert the Cube chart values and the
  secret) until this has run in production for a while, then they can go.
- Server-side Iceberg attach is a MotherDuck preview, and Horizon isn't on its
  list of tested catalogs (Polaris, which Horizon is built on, is). It has
  been confirmed working against Horizon, including the empty client id and
  role scope; the bootstrap's smoke test is where a regression would surface.
- Iceberg reads on MotherDuck see the latest committed snapshot at query
  time. Freshness is unchanged: each cube's `refresh_key` still decides when
  Cube Store rebuilds.
