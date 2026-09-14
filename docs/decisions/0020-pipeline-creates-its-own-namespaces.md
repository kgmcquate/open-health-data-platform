# 0020 — dbt and dlt create the catalog namespaces, not Terraform

**Status:** **Superseded by [ADR-0021](0021-terraform-creates-the-namespaces-again.md)**
— Horizon's Iceberg REST catalog turns out not to implement namespace creation
for external engines at all, so the premise below (the pipeline opens its own
namespaces the same way it opens tables) doesn't hold. Terraform creates them
again.
**Relates to:** narrows [ADR-0011](0011-snowflake-horizon-catalog.md)'s
"Terraform-managed" scope and amends the Terraform surface of
[ADR-0013](0013-per-layer-snowflake-databases.md) and
[ADR-0019](0019-iceberg-on-s3-duckdb-dbt.md). The layout itself — one database
per medallion layer, `RAW.<SOURCE>` / `CLEAN.STG_<SOURCE>` / `CURATED.CORE` /
`CURATED.<MART>` — is unchanged. Only who creates it changes.

## Context

The namespaces existed twice. Terraform declared them from two variables
(`lakehouse_sources`, `lakehouse_marts`) and dbt derived the same names from its
model tree via `generate_schema_name.sql`, with `ohdp_ingestion.naming` as a
third copy for dlt. Adding a mart meant a dbt folder *and* a `terraform.tfvars`
entry *and* an apply, in that order, or the build failed on a missing namespace
that dbt was about to create anyway.

It was about to create it anyway because the pipeline already had the privilege
and the code path. dbt runs `CREATE SCHEMA IF NOT EXISTS` for every schema in a
run before building into it; dbt-duckdb sends that to Horizon as a CREATE
NAMESPACE on the attached Iceberg catalog (DuckDB gained REST-catalog
`CREATE SCHEMA` in v1.5.3, per ADR-0019), and the pipeline role has
`CREATE SCHEMA` on all three layer databases. dlt does the same for its RAW
dataset. Terraform's `snowflake_schema` resources were creating objects that
would have appeared without them.

What Terraform's copy bought was legibility and grantability: empty namespaces
visible in the account from the first apply, and `ALL SCHEMAS` grants with
something to grant on. Neither survived scrutiny. There is exactly one role in
the account, and it *owns* whatever it creates, so the grants are moot for
anything the pipeline opens; and namespaces that exist but hold no tables are
not information anyone was reading.

## Decision

Terraform manages the external volume, the layer databases, the identity and
the grants. It does not manage namespaces. `snowflake_schema.namespace`,
`local.snowflake_namespaces`, `var.lakehouse_sources` and `var.lakehouse_marts`
are deleted.

The namespace layout has one definition per writer, and each writer creates what
it names: `generate_schema_name.sql` for CLEAN and CURATED, and
`ohdp_ingestion.naming` for dlt's RAW datasets. `CREATE SCHEMA` in the database
grant is the whole Terraform contribution.

Storage is unaffected, and this is the point worth being explicit about:
`CATALOG` and `EXTERNAL_VOLUME` are *database* defaults, and Iceberg tables
resolve both through table → schema → database. A namespace created over the
REST API sets neither and still yields Iceberg tables in our own S3 bucket. The
external volume therefore stays in Terraform, where the storage decision is
made — ADR-0019's warning about silently writing to Snowflake-managed storage is
unchanged and still lives at that resource.

## Consequences

- **Adding a mart or a source is a one-place change.** A dbt folder, or a source
  in `ohdp_ingestion`. No apply, no ordering constraint between the two.
- **Nothing prunes a namespace.** Rename a mart and `CURATED.<OLD>` is orphaned
  until someone drops it by hand. Terraform used to delete it on the next apply
  and no longer sees it. This is the real cost of the decision; accepted because
  an empty namespace is inert, and because Terraform's pruning was also its
  sharpest edge (see the migration note).
- **Namespaces appear on first build, not first apply.** OpenMetadata ingestion
  and anything else enumerating the catalog sees nothing until the pipeline has
  run once.
- **The `ALL SCHEMAS` / `ALL ICEBERG TABLES` grants are gone too**, down to
  three grants per database from five. Every namespace and table is now created
  by the pipeline role, which owns what it creates; `ALL` would only restate what
  ownership already grants. `FUTURE` stays for the object created by some other
  role — a manual fix as ACCOUNTADMIN — which it covers at creation time.

### Migration — the first apply destroys data, by choice

`snowflake_schema.namespace` was in state, so deleting the resource makes the
next apply **drop those namespaces and every Iceberg table in them**. That is
the intended path, not an accident: the alternative (a `removed` block with
`lifecycle { destroy = false }`) would leave every existing namespace owned by
the applying role rather than the pipeline, which is the ownership split the
grants above just stopped accommodating. Dropping them makes the account's state
match the decision, at the cost of one rebuild.

What that costs, concretely:

- **CLEAN and CURATED are derived** — `dbt build` reconstructs them from RAW.
- **RAW is re-ingested from upstream.** `_dlt_pipeline_state` lives in the RAW
  schema and goes with it, so dlt starts its incremental cursors over. Sources
  whose upstream serves full history (Socrata, CDC) come back intact; anything
  whose upstream retention is shorter than the history we had held does not.
- **The files, not just the metadata.** These are Snowflake-catalogued Iceberg
  tables, so `DROP SCHEMA` cascades to the tables and the external volume's
  files are purged once Time Travel expires — an undrop window, not a backup.

Run it deliberately: read the plan, confirm the `snowflake_schema` destroys are
the only ones in it, apply, then trigger a full pipeline run before anyone reads
a dashboard.
