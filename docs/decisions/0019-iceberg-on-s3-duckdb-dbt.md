# 0019 — Iceberg on S3 with a Glue REST catalog; DuckDB builds, Snowflake reads

**Status:** Accepted
**Relates to:** supersedes [ADR-0012](0012-native-snowflake-tables.md) (native
Snowflake tables), [ADR-0013](0013-per-layer-snowflake-databases.md) (one
Snowflake database per layer) and [ADR-0014](0014-snowflake-only-compilation.md)
(Snowflake-only compilation). Returns to the medallion *lakehouse* shape of
[ADR-0010](0010-iceberg-medallion-lakehouse.md) — Iceberg tables, layer-prefixed
namespaces, dbt on DuckDB — but with a managed catalog and object store instead
of a self-hosted Polaris on Spaces, and with none of the bespoke write code
that decision needed. [ADR-0011](0011-snowflake-horizon-catalog.md) (Horizon
Catalog) stays superseded. Snowflake is not retired: it keeps serving Cube and
Streamlit ([ADR-0003](0003-cube-core-over-dbt-semantic-layer.md),
[ADR-0015](0015-streamlit-over-superset.md) are unaffected).

## Context

ADR-0012 moved everything to native Snowflake tables after the Iceberg attempt
collapsed under an accumulation of integration defects — vended-credential
scoping, an account-wide network policy, an OAuth `invalid_scope`, a `403` on
`CREATE ICEBERG TABLE`, and dlt's inability to create a table without passing an
explicit `location` that Snowflake-managed storage rejects. ADR-0014 then
deleted the DuckDB targets so there was one dialect instead of two.

That left a warehouse the project pays for on every build, with the transform
layer locked to one vendor's SQL, and the tables invisible to anything without
Snowflake credentials. The three things that made Iceberg fail in ADR-0012 have
each since stopped being true:

- **Writing Iceberg from DuckDB is now first-class.** DuckDB's `iceberg`
  extension gained write support against REST catalogs in v1.4 and filled in
  the rest in v1.5.3 — `CREATE`/`DROP SCHEMA` and `TABLE`, `INSERT`, `UPDATE`,
  `DELETE`, `MERGE INTO`, and `ALTER TABLE` including `RENAME TO`, which is
  what dbt's table materialization is built on.
- **dlt writes Iceberg through a real catalog.** Its `filesystem` destination
  takes `table_format="iceberg"` and a configurable pyiceberg catalog, and
  handles schema evolution (`union_by_name`) and pipeline state itself. The
  hand-rolled `ohdp_ingestion.iceberg.commit` of ADR-0010 has no job left.
- **Snowflake reads an external catalog without per-table DDL.** A
  *catalog-linked database* over an Iceberg REST catalog integration syncs
  namespaces and tables automatically, and as of Snowflake's 2026-04-30 change
  case-insensitive catalogs (Glue among them) no longer force double-quoted
  lowercase identifiers.

The remaining question was storage. Snowflake cannot use DigitalOcean Spaces
for Iceberg — its external volumes are S3/Azure/GCS only — so the lakehouse has
to live on AWS S3 even though the cluster stays on DigitalOcean.

## Decision

One Iceberg catalog holds every table. **DuckDB writes it, Snowflake reads it.**

### Storage and catalog

- **Storage**: a single AWS S3 bucket (`ohdp-lakehouse`), every table's data and
  metadata under it.
- **Catalog**: the **AWS Glue Data Catalog**, addressed through its Iceberg REST
  endpoint (`https://glue.<region>.amazonaws.com/iceberg`, SigV4). Chosen over
  S3 Tables because dlt creates tables with an explicit `location` — the exact
  thing Snowflake-managed storage rejected in ADR-0012, and that an S3 Tables
  bucket rejects for the same reason — and over a self-hosted Lakekeeper or
  Polaris because ADR-0010 already paid for that (a JVM service, a Postgres
  database, a console, a second oauth2-proxy, ~1 GB against §4's budget) and
  ADR-0012 wrote off the result.
- **Namespaces are flat and layer-prefixed**, because Glue's are:
  `raw_<source>`, `clean_<source>`, `core`, `mart_<name>`. ADR-0013's
  one-database-per-layer split does not survive — there is one catalog — but the
  layer/source/mart structure does. `ohdp_ingestion.naming` is still the single
  definition of these names.
- The catalog is known as `lakehouse` to **both** engines: it is DuckDB's
  `ATTACH ... AS lakehouse` alias and the name of Snowflake's catalog-linked
  database. That equality is load-bearing: one compiled dbt manifest then
  describes relations (`lakehouse.core.hospital_utilization_daily`) that both
  engines resolve, which is what lets Cube keep generating its SQL from the
  manifest (`cube-dbt`) while DuckDB does the building.

### Write side

- **raw**: dlt's `filesystem` destination on `s3://`, `table_format="iceberg"`,
  catalog config injected through `dlt.config` from `ohdp_shared.settings`
  (nothing to mount into the run pod). Append-only, schema-evolving, cursor
  state in the destination — unchanged behaviour, different storage.
- **clean/core/marts**: **dbt-duckdb**. The profile attaches the Glue catalog
  and every model is an ordinary `materialized="table"` in it. There is no
  custom write plugin — the ADR-0010 `ohdp_ingestion.dbt.iceberg` plugin exists
  only because DuckDB could not write Iceberg then, and it can now.
- The clean models drop `incremental`/`merge` for a plain table rebuild. They
  were never compute-incremental (the dedupe is a `qualify` over all of raw);
  only the write was, and a full rebuild is the DuckDB-Iceberg path with the
  least surface area. Revisit per-model if a raw table ever gets big enough for
  the rewrite to matter.
- DuckDB is not a warehouse here and stores nothing: `path: ":memory:"`. Losing
  the process loses nothing.

### Read side

Snowflake keeps the query workloads that want a warehouse behind them —
Cube's metric queries and Streamlit's ad-hoc ones — reading the same tables
through an external volume + Iceberg REST catalog integration + catalog-linked
database. Read-only: `CREATE TABLE` and the DML grants come off the query role,
because nothing in Snowflake writes any more.

### Dialect

The transform layer is DuckDB SQL. The models needed four substitutions, each
now a macro so the intent stays visible: `initcap` (no DuckDB builtin —
split/rebuild), `div0`, `try_to_date` (`try_cast(... as date)`),
`date_from_parts` (`make_date`), plus `_dlt_load_id` parsing. `* exclude`,
`qualify` and `try_cast` were already valid in both.

### Credentials

- The pipeline authenticates to AWS as one IAM user (Terraform-created access
  key), used by dlt, pyiceberg's SigV4 signing and DuckDB's S3 secret alike.
- **Dagster's compute logs move from Spaces to the same AWS account.**
  `S3ComputeLogManager` takes no access-key config — it reads
  `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` off the process environment — and
  those variables now have to hold the real AWS credentials for pyiceberg's
  signer. Two different S3 credentials cannot coexist in one pod, so the
  compute-log bucket follows the lakehouse to AWS rather than the lakehouse
  bending around it. Spaces keeps the tfstate backend and the (still-stubbed)
  Postgres backups.
- Snowflake's two AWS roles (one for the external volume's S3 access, one for
  SigV4-signing Glue) are created by Terraform but need a **second apply** to
  finish: their trust policies name an IAM user ARN and external ID that only
  exist once the external volume and catalog integration do. `terraform apply`,
  read them out of Snowflake, set two variables, apply again — documented in
  `platform/terraform/README.md`.

## Consequences

- **Existing Snowflake data is abandoned, not migrated** — the same call
  ADR-0011 and ADR-0012 each made. The RAW/CLEAN/CURATED databases go away with
  the Terraform that made them; re-running the loads against the catalog is the
  whole migration path.
- **CI still cannot run `dbt build`.** `dbt parse` is offline-safe and stays the
  gate, as ADR-0014 left it. The difference is that a live target is now cheap
  to stand up (S3 + Glue, no warehouse), so wiring a real build into CI is a
  smaller decision than it was.
- **The test suite got closer to production, not further.** `test_raw_load.py`
  used to stub the destination with SQLite over dlt's `sqlalchemy` destination;
  it now writes actual Iceberg tables to a temp directory through a SQLite
  pyiceberg catalog, and reads them back through the catalog. The only
  substitutions are *where* the lake is and *which* catalog implementation
  backs it.
- **Two engines again, but not two dialects.** ADR-0014's complaint was that
  every model had to stay portable across DuckDB and Snowflake. That does not
  come back: models are compiled for DuckDB only. Snowflake reads the *tables*,
  not the project, and the manifest is compiled once against the one target.
- **The asset graph is renamed**: `snowflake/…` becomes `lakehouse/…`, keyed
  `[lakehouse, <catalog>, <namespace>, <table>]`, kinds `dbt` + `iceberg`. The
  `openmetadata_snowflake_sync` ingestion is unchanged — it points at the
  catalog-linked database, so the catalog still sees every table.
- **New failure mode: two writers, one catalog.** dlt and dbt commit through
  the same Glue catalog. Iceberg's per-table ACID means neither can half-write
  a table, and `maxConcurrentRuns: 1` (§3/§4) still serializes the pipeline —
  but a Snowflake-side write would now be a real conflict, which is part of why
  the query role is read-only.
- **AWS becomes a third provider** (after DigitalOcean and Cloudflare), with
  its own Terraform provider, credentials and bill. Cost is S3 storage plus
  Glue requests — under a dollar a month at this data volume, against the
  Snowflake credits each dbt build used to burn.
