# 0021 — Terraform creates the catalog namespaces again

**Status:** Accepted
**Supersedes:** [ADR-0020](0020-pipeline-creates-its-own-namespaces.md). The
layout is unchanged — one database per medallion layer, `RAW.<SOURCE>` /
`CLEAN.STG_<SOURCE>` / `CURATED.CORE` / `CURATED.<MART>` — only who creates it
changes, back to where [ADR-0011](0011-snowflake-horizon-catalog.md) and
[ADR-0013](0013-per-layer-snowflake-databases.md) originally put it.

## Context

ADR-0020's premise was that the pipeline could open its own namespaces the
same way it creates and writes Iceberg tables: dlt's `create_namespace_if_not_exists`
over pyiceberg's REST catalog client, and dbt-duckdb's `CREATE SCHEMA`
forwarded to Horizon as a REST `createNamespace` call.

That premise doesn't hold. Snowflake's Horizon Iceberg REST catalog supports
the table operations an external engine needs — `loadTable`, `createTable`,
writes — but not namespace creation. `POST
.../polaris/api/catalog/v1/RAW/namespaces` 404s, confirmed against current
Snowflake documentation: a schema has to already exist as an ordinary
Snowflake schema before an external REST client can write Iceberg tables into
it. Nothing over the open protocol can bring a namespace into existence in a
Snowflake-catalogued database; only SQL `CREATE SCHEMA`, run inside Snowflake,
can.

dlt hit this first — a 404 out of `pyiceberg/catalog/rest/create_namespace`
ingesting a RAW source. dbt-duckdb's path was never exercised past this point,
but there's no reason to expect DuckDB's REST-catalog client gets treated any
differently by the same endpoint.

## Decision

Terraform creates every namespace again: `snowflake_schema.namespace`,
`local.snowflake_namespaces`, `var.lakehouse_sources` and `var.lakehouse_marts`
are restored to `platform/terraform/{snowflake,variables}.tf`, unchanged in
shape from before ADR-0020. The `ALL SCHEMAS` / `ALL ICEBERG TABLES` grants
come back alongside them — the pipeline role doesn't own a namespace Terraform
created, so it needs the grant ADR-0020 dropped as redundant.

dlt and dbt-duckdb no longer attempt namespace creation at all: not because
it's undesirable, but because the REST protocol they write through has no
operation for it. `ohdp_ingestion.socrata.source.iceberg_rest_sink` drops its
`create_namespace_if_not_exists` call outright rather than reaching for SQL as
a workaround — Terraform is the one place this is declared, and a second
declaration (a pipeline-side `CREATE SCHEMA IF NOT EXISTS` over a SQL
connection) would just be ADR-0020's two-copies problem again, with SQL
standing in for REST.

## Consequences

- **Adding a mart or a source is a Terraform change again.** A dbt folder or a
  new `ohdp_ingestion` source now also needs a `terraform.tfvars` entry and an
  apply, in that order — the ordering constraint ADR-0020 removed is back,
  because it's what actually works against Horizon.
- **Namespaces are pruned again, and destructively.** Renaming a mart drops its
  old `CURATED.<OLD>` schema and every Iceberg table in it on the next apply.
  This is the trade ADR-0020 opted out of; opting back in is deliberate here,
  not an oversight.
- **Namespaces exist from the first apply**, not the first pipeline run —
  OpenMetadata ingestion and anything else enumerating the catalog sees the
  full structure immediately, empty schemas included.
- **Low migration cost, but check before applying.** The namespace-creation
  calls ADR-0020 relied on 404'd, so no RAW/CLEAN/CURATED namespace was ever
  successfully opened by the pipeline — there's nothing outside Terraform's
  state for the new `snowflake_schema.namespace` resources to collide with.
  If a namespace *was* created out-of-band in some environment (by hand, or by
  an earlier code path), `terraform apply` will fail trying to `CREATE SCHEMA`
  over an existing one; `terraform import` it into the matching
  `snowflake_schema.namespace["..."]` address first.
