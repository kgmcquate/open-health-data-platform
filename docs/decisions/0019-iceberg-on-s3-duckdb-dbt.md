# 0019 — Snowflake as the Iceberg catalog; DuckDB builds, Snowflake serves

**Status:** Accepted
**Relates to:** supersedes [ADR-0012](0012-native-snowflake-tables.md) (native
Snowflake tables) and [ADR-0014](0014-snowflake-only-compilation.md)
(Snowflake-only compilation). **Keeps
[ADR-0013](0013-per-layer-snowflake-databases.md) intact** — one database per
medallion layer, same databases, same schemas, same table names. Reinstates
[ADR-0011](0011-snowflake-horizon-catalog.md)'s catalog choice — Snowflake's own
Iceberg catalog — and [ADR-0010](0010-iceberg-medallion-lakehouse.md)'s medallion
shape, without the custom dbt write plugin that decision needed. Cube
([ADR-0003](0003-cube-core-over-dbt-semantic-layer.md)) and Streamlit
([ADR-0015](0015-streamlit-over-superset.md)) are unaffected: they still query
Snowflake over SQL.

## Context

ADR-0012 moved everything to native Snowflake tables after the
Iceberg-via-Horizon attempt collapsed under an accumulation of integration
defects — a `403` on `CREATE ICEBERG TABLE` traced to a missing external-volume
grant, an `invalid_scope` OAuth failure from pyiceberg's token exchange, a
network policy that had to be attached account-wide, and dlt's Iceberg support
insisting on an explicit table `location` that Snowflake-managed storage
rejected. ADR-0014 then deleted the DuckDB targets so there was one SQL dialect
instead of two.

That left a warehouse the project pays for on every build, with the transform
layer locked to one vendor's SQL, and the tables reachable only through
Snowflake credentials. Three things have changed since:

- **External engines can now write Iceberg tables Snowflake catalogues.**
  Writing through Snowflake Horizon's Iceberg REST endpoint went to preview in
  March 2026 and GA in May 2026. When ADR-0011 and ADR-0012 were written,
  external engines could only *read* — which is most of why that design had so
  little to offer for the trouble it cost.
- **DuckDB writes Iceberg.** Its `iceberg` extension gained REST-catalog writes
  in v1.4 and filled in the rest in v1.5.3 — `CREATE`/`DROP SCHEMA` and `TABLE`,
  `INSERT`, `UPDATE`, `DELETE`, `MERGE INTO`, and `ALTER TABLE` including
  `RENAME TO`, which is what dbt's table materialization is built on. The custom
  `ohdp_ingestion.dbt.iceberg` plugin of ADR-0010 exists only because none of
  that did.
- **Credential vending works on S3.** Horizon hands an external engine
  short-lived, scoped storage credentials. ADR-0010 had to *suppress* that
  header, because Polaris could not subscope credentials for non-AWS Spaces.

Storage has to be AWS S3: Snowflake's external volumes accept S3, Azure and
GCS, and DigitalOcean Spaces is not among them. The cluster stays on
DigitalOcean.

## Decision

**Snowflake is the Iceberg catalog. DuckDB builds the tables; Snowflake serves
them.**

### Snowflake holds the metadata; S3 holds the bytes

The catalog is Snowflake's. The *storage* is not, and that distinction is the
one most easily lost: `CATALOG = 'SNOWFLAKE'` says who owns the table metadata,
which is a separate question from where the files sit. Every layer database
carries an `EXTERNAL_VOLUME` over our own S3 bucket, and Iceberg tables inherit
it through table → schema → database, so every table dlt and dbt create — via
the REST API, passing neither setting — lands there.

Snowflake's own managed storage is never used. ADR-0011 did use it, and that is
part of what went wrong then (see the open risk below). Dropping the
`EXTERNAL_VOLUME` would not fail loudly; it would quietly start writing new
tables somewhere we do not control, which is why `platform/terraform/snowflake.tf`
says so at the resource.

### A Snowflake database is a catalog; its schemas are namespaces

That equivalence is the whole reason this migration changes so few names.
Iceberg namespaces nest, and Snowflake's nest exactly one level deep —
database, then schema — so ADR-0013's layout maps onto the catalog without
being flattened into it:

    RAW.<SOURCE>          RAW.CDC            written by dlt
    CLEAN.STG_<SOURCE>    CLEAN.STG_CDC      written by dbt-duckdb
    CURATED.CORE          conformed          written by dbt-duckdb
    CURATED.<MART>        CURATED.RESPIRATORY

Each layer database is created with `CATALOG = 'SNOWFLAKE'` and the
`EXTERNAL_VOLUME` above, and Horizon serves all three over the open Iceberg
REST protocol at
`https://<account>.snowflakecomputing.com/polaris/api/catalog`.

Each database is therefore one object with three names that are deliberately
the same string: the Snowflake database, the Iceberg catalog, and a DuckDB
`ATTACH ... AS RAW` alias. Keeping them equal is load-bearing — one compiled
dbt manifest then describes relations
(`CURATED.CORE.HOSPITAL_UTILIZATION_DAILY`) that DuckDB resolves through the
REST catalog and Snowflake resolves as ordinary SQL, which is what lets Cube
keep generating its queries straight from the manifest (`cube-dbt`) while
DuckDB does the building.

dbt attaches three catalogs rather than one, because DuckDB's qualified names
have exactly three parts (`catalog.schema.table`): a namespace nested any
deeper than one level has nowhere to go in the SQL. `ohdp_ingestion.naming` is
still the single definition of all of these names.

### Everything is upper case

Not a style preference. Snowflake requires an external engine reaching it
through Horizon to address the database, namespaces and tables **in all
capitals**, whatever case they were created with; it is also what an unquoted
identifier folds to in Snowflake SQL, so Cube and Streamlit resolve the same
names without quoting. Three places had to agree, and now do:
`ohdp_ingestion.naming`, dbt's
`generate_database_name`/`generate_schema_name`/`generate_alias_name`, and dlt —
which needed a naming convention of its own (`ohdp_ingestion.sql_upper`), since
dlt ships only lower-casing ones. In practice this changes nothing about the
*stored* names: Snowflake already folded the old lower-case dbt project's
unquoted identifiers to upper case.

Asset keys stay lower case throughout. They are labels in the Dagster graph, not
identifiers, and should not move if this convention ever changes again.

### Write side

- **raw**: dlt's `filesystem` destination with `table_format="iceberg"`,
  committing to the `RAW` catalog through Horizon. Auth is OAuth2 client-credentials — the Snowflake
  user as client id, a **programmatic access token** as the secret, `scope =
  session:role:<ROLE>`. dlt handles schema evolution (`union_by_name`), the
  write dispositions and the incremental cursor, as it does against any
  destination.
- **clean/core/marts**: **dbt-duckdb**. The profile attaches `CLEAN` and
  `CURATED` (and `RAW`, to read sources), and every model is an ordinary
  `materialized="table"` in whichever its `+database` names. No custom write
  plugin. DuckDB takes catalog-vended credentials, so it holds no AWS key.
- The clean models drop `incremental`/`merge` for a plain table rebuild. They
  were never compute-incremental (the dedupe is a `qualify` over all of raw);
  only the write was, and a full rebuild is the DuckDB-Iceberg path with the
  least surface area. Revisit per-model if a raw table ever gets big enough for
  the rewrite to matter.
- DuckDB is not a warehouse here and stores nothing: `path: ":memory:"`. Losing
  the process loses nothing.

### Read side

Cube and Streamlit are unchanged, full stop — they connect to Snowflake with
the RSA key pair and query `CURATED.CORE.*` / `CURATED.<MART>.*`, the same
relations under the same names as before. There is no
catalog integration, no catalog-linked database, no external table DDL: these
*are* Snowflake tables. OpenMetadata's Snowflake connector likewise keeps
working, crawling the same three databases it always did.

### Two credentials, one identity

The two protocols authenticate differently, so the one service user carries
both: an **RSA key pair** for the SQL connector, and a **PAT** for the REST
catalog. ADR-0012 deleted the PAT resource on the grounds that nothing did an
OAuth2 exchange any more; something does again, and it is the whole write path.
PATs only work for a user covered by a network policy, which is what finally
gives the account-wide policy a job — ADR-0012 kept it only because it was
already proven.

### Dialect

The transform layer is DuckDB SQL. The models needed four substitutions, each
now a macro so the intent stays visible: `initcap` (no DuckDB builtin —
split/rebuild), `div0`, `try_to_date` (`try_cast(... as date)`),
`date_from_parts` (`make_date`), plus `_dlt_load_id` parsing. `* exclude`,
`qualify` and `try_cast` were already valid in both.

## Consequences

- **Existing Snowflake data is abandoned, not migrated** — the same call
  ADR-0011 and ADR-0012 each made. The RAW/CLEAN/CURATED databases are replaced
  in place by catalog-enabled ones of the same names; re-running the loads is
  the whole migration path.
- **The names do not move.** Databases, schemas and tables keep the identifiers
  they had as plain Snowflake tables, so Cube's models, Streamlit's queries and
  anything else addressing `CURATED.CORE.*` are unaffected by what is now
  backing them.
- **Snowflake is still in the stack, and still costs money** — but only for
  serving. No warehouse runs during a build: that is DuckDB, in the pipeline
  pod. The pod is now the compute, so its memory limit matters in a way it
  didn't when it only shipped SQL to a warehouse (ARCHITECTURE.md §4).
- **The pipeline keeps one AWS credential for one job.** dlt writes each raw
  table's Parquet into the volume's bucket itself, through fsspec, which cannot
  consume vended credentials. Everything else — the catalog, DuckDB's file
  access — goes through Snowflake. Dagster's compute logs therefore stay on
  Spaces: nothing contends for boto3's environment variables.
- **The PAT expires** (365 days, Snowflake's cap), unlike the RSA key pair.
  Rotation is a `terraform apply -replace` plus a secret push, documented in
  `platform/terraform/README.md` — but it is a deadline the project did not have
  before.
- **A second apply is still required.** The external volume's IAM trust policy
  names an ARN and external ID that only exist once Snowflake has created the
  volume. One round-trip, documented in the same README.
- **Open risk: dlt passes an explicit table `location` on create.** ADR-0012
  recorded this being rejected — but by *Snowflake-managed storage*, which
  ADR-0011 had opted into and this ADR does not. A location under an external
  volume we own is a materially different case from one under a path Snowflake
  picks for itself, and external writes have gone from unsupported to GA since.
  So the odds are better than that note suggests. It is still unverified
  against a live account, and still the first thing to check if raw loads fail
  on table creation. If Snowflake rejects it anyway, the fix is to pre-create
  each raw table with Snowflake DDL (the column set is in the dataset config)
  so dlt takes its `evolve_table` path, which passes no location. The dbt
  layers have no such exposure — DuckDB lets the catalog choose.
- **The test suite got closer to production, not further.** `test_raw_load.py`
  used to stub the destination with SQLite over dlt's `sqlalchemy` destination;
  it now writes actual Iceberg tables to a temp directory through a SQLite
  pyiceberg catalog, reads them back through the catalog, and asserts the
  upper-casing contract. The only substitutions are *where* the lake is and
  *which* catalog implementation backs it.
- **Two engines again, but not two dialects.** ADR-0014's complaint was that
  every model had to stay portable across DuckDB and Snowflake. That does not
  come back: models compile for DuckDB only. Snowflake reads the *tables*, not
  the project.
- **The asset graph is renamed**: `snowflake/…` becomes `lakehouse/…`, keyed
  `[lakehouse, <database>, <schema>, <table>]` as before, kinds `dbt` +
  `iceberg`. Keys stay lower case even though the SQL identifiers are upper
  case — a key is a graph label, not an identifier.
- **Two writers, one catalog.** dlt and dbt both commit through Horizon.
  Iceberg's per-table ACID means neither can half-write a table, and
  `maxConcurrentRuns: 1` (§3/§4) still serializes the pipeline. Snowflake itself
  writes nothing — the role has the privilege, because it is one role, but
  nothing in the design uses it.
