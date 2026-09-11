# 0011 — Snowflake Horizon Catalog replaces Polaris

**Status:** Accepted
**Relates to:** supersedes the *catalog* and *storage* decisions in
[ADR-0010](0010-iceberg-medallion-lakehouse.md). The medallion layout, the
namespace scheme, the dbt-duckdb writer plugin and the publish step are all
unchanged — this ADR swaps what is on the other end of `load_catalog()`.

## Context

ADR-0010 self-hosted Apache Polaris as the Iceberg REST catalog. In practice it
was the most expensive thing in the stack to keep alive:

- Polaris cannot subscope credentials for non-AWS S3, so it vends nothing. The
  pipeline had to suppress pyiceberg's default
  `X-Iceberg-Access-Delegation: vended-credentials` header and carry its own
  Spaces keys, *and* Polaris still needed a second copy of those keys to write
  the first `metadata.json` on a non-staged create.
- Bootstrap was three moving parts: an admin-tool init container for the schema
  and realm, a post-install script for the catalog, and generated secrets in
  three shapes (`polaris-persistence`, `polaris-principal`, `polaris-bootstrap`).
- It cost a JVM (~1 GB) plus a Postgres database against the §4 budget, and the
  admin UI needed a vendored chart, an off-cycle GHCR image build and a second
  oauth2-proxy with its own hostname and OAuth redirect URI.

That is a lot of surface for a component whose entire job is to answer "where is
this table's metadata".

## Decision

Use **Snowflake's Horizon Catalog** as the Iceberg REST catalog, over the same
`pyiceberg` API. DuckDB remains the only compute — Snowflake is a catalog and a
bucket here, never a query engine. Snowflake objects are provisioned in
Terraform (`platform/terraform/snowflake.tf`), not Helm.

### The mapping

| Iceberg | Snowflake |
|---|---|
| `warehouse` (REST property) | a **database** — `OHDP`. Not a virtual warehouse. |
| namespace | a **schema** in that database |
| table | an Iceberg table |

Endpoint: `https://<org>-<account>.snowflakecomputing.com/polaris/api/catalog`
(Horizon embeds Polaris, hence the path).

### Storage moves into Snowflake — this was forced, not preferred

Snowflake's external-engine path **does not support S3-compatible non-AWS
storage**. DigitalOcean Spaces therefore cannot hold these tables: the choice
was Snowflake-managed storage, or an external volume on real AWS S3 (a new cloud
account, the `aws` provider, an IAM role and a trust policy).

We took Snowflake-managed storage — `external_volume` is left unset, Snowflake
picks the location, and it vends short-lived per-table credentials to the REST
client. That is what deletes the whole Polaris credential dance: no storage keys
given to the catalog, no suppressed delegation header, no `s3.*` properties in
`catalog_properties()`, no bucket policy.

Spaces keeps its other two jobs: the published DuckDB snapshots + `current.json`
(ADR-0002) and the nightly `pg_dump`. It also gains a third, below.

### Auth: a PAT on a SERVICE user

`OHDP_ICEBERG_CREDENTIAL` is `"<user>:<pat>"` and `OHDP_ICEBERG_SCOPE` is
`session:role:OHDP_PIPELINE`. pyiceberg's existing OAuth2 `client_credentials`
exchange handles it unchanged — the settings kept their names and only their
values moved. Key-pair JWT was the alternative; it needs a `grant_type=jwt-bearer`
exchange pyiceberg does not implement, so it would have meant a custom token shim.

Two consequences worth writing down:

- **Snowflake refuses to issue or accept a PAT for a SERVICE user that is not
  subject to a network policy.** `snowflake_network_policy.pipeline` exists for
  that reason and defaults to `0.0.0.0/0`. Narrowing it to the DOKS egress IPs
  needs a stable egress first — node recycles and pool resizes change those
  addresses, and a stale entry 401s every asset in the run.
- **A PAT expires, at 365 days maximum.** Rotation is a standing calendar item:
  bump `snowflake_pat_keeper`, apply, re-set the `SNOWFLAKE_ICEBERG_CREDENTIAL`
  repo secret, re-deploy.

### The raw loader stops writing Iceberg through dlt

dlt's `filesystem` + `table_format=iceberg` destination passes an explicit
`location=` when it creates a table. Snowflake-managed storage assigns the table
location itself and rejects one from the client, so that path cannot work here.

The raw loader splits in two:

1. dlt extracts, normalizes and keeps the incremental cursor, writing **only the
   current run's delta** to a staging prefix (`s3://<bucket>/_dlt_staging/<source>/`,
   `write_disposition="replace"`). Staging is on Spaces rather than a local dir
   because dlt keeps its pipeline state in the destination, and that state is
   what makes the cursor survive a pod restart.
2. `ohdp_ingestion.iceberg.commit` lands the staged Arrow table in the catalog —
   `append` when there was a cursor (raw is history), `overwrite` when the
   dataset is re-fetched wholesale.

`commit()` is the write path the dbt plugin already used, lifted out of it, so
create / `union_by_name` schema evolution / align-and-cast / upsert now behave
identically in every layer. That matters most for raw, where ~150 scraped
datasets drift columns without warning.

An empty staging table is a no-op rather than an `overwrite`: "nothing changed
upstream" must not empty the table.

### Identifier casing

Terraform creates the schemas **lowercase** to match what the pipeline sends over
REST (`raw_healthdata_gov`, and dbt model names like `stg_datasets`). The REST
protocol passes names through verbatim, so Snowflake stores them as quoted
lowercase identifiers. The pipeline needs no casing translation; the cost is that
ad-hoc SQL must quote them:

```sql
select * from ohdp."clean_healthdata_gov"."stg_datasets";
```

The database (`OHDP`) is uppercase — it is only ever named in SQL and in the REST
`warehouse` property, never built from a Python identifier.

## Consequences

- **Removed:** `values/polaris.yaml`, `values/polaris-console.yaml`,
  `values/oauth2-proxy-polaris.yaml`, `vendor/polaris-console/`,
  `scripts/polaris-bootstrap.sh`, `scripts/vendor-polaris-console.sh`,
  `build-polaris-console.yml`, the three generated Polaris secrets, the
  `polaris` Postgres database, the `polaris` DNS record and its Google OAuth
  redirect URI. ~1.5 GB back against §4's budget.
- **New external dependency and a new bill.** Snowflake now holds the lake's
  data files. Storage is billed by Snowflake; the catalog and metadata
  operations need no virtual warehouse, so there is no compute line for the
  pipeline itself. `OHDP_WH` (XSMALL, auto-suspend 60s) exists only for ad-hoc
  SQL.
- **The Polaris console is gone.** Catalog inspection is Snowsight, which is
  behind Snowflake's own auth — one fewer login wall to run, but also no
  in-cluster admin UI.
- **Terraform now owns account-level Snowflake objects** and needs
  `ACCOUNTADMIN` (or equivalent). Its credentials come from the environment
  (`SNOWFLAKE_USER`, `SNOWFLAKE_ROLE`, `SNOWFLAKE_PRIVATE_KEY`), never
  `terraform.tfvars`. The PAT lands in Terraform state — that state lives in a
  private Space, and rotation is a `snowflake_pat_keeper` bump.
- **Existing lake data is not migrated.** The Iceberg tables under
  `s3://ohdp-warehouse/{raw,clean,curated}/` are orphaned by this change; the
  catalog that described them is gone. M0 curated data does not exist yet, so
  the intended path is to re-run the raw loads against the new catalog and
  delete the old prefixes once that is green.
- **dbt profile simplifies.** No `httpfs`/`iceberg` DuckDB extension and no s3
  settings — DuckDB never reaches the lake itself.
- Snowflake's external-write path does not support equality deletes or
  `CREATE TABLE AS SELECT`. Neither is used: pyiceberg's `upsert` rewrites data
  files (copy-on-write) and every table is created empty and then written.
