# 0012 — Native Snowflake tables replace the Iceberg lake

**Status:** Accepted
**Relates to:** supersedes [ADR-0010](0010-iceberg-medallion-lakehouse.md) and
[ADR-0011](0011-snowflake-horizon-catalog.md) in full, and retires the
publish-and-replicate mechanism from [ADR-0002](0002-publish-and-replicate-duckdb.md)
(see Consequences). The medallion layout and the schema-naming scheme are
unchanged — `raw_<source>`, `clean_<source>`, `core`, `mart_<name>` are still
what things are called; this ADR only changes what they are.

## Context

Getting Iceberg-via-Horizon-Catalog working end to end surfaced a long chain of
distinct problems, each requiring its own fix: a GitHub secret named after the
wrong variable, a network policy that has to be attached account-wide rather
than to the service user (undocumented outside Horizon's external-engine
access page), an `invalid_scope` OAuth failure caused by pyiceberg sending a
`client_id` Snowflake's token endpoint silently rejects, and finally a `403
Forbidden` on `CREATE ICEBERG TABLE` traced to a missing external-volume grant
that several rounds of Snowflake's own documentation didn't resolve
confidently. Separately, dlt's own "Iceberg destination" turned out to always
pass an explicit `location` on create, which Snowflake-managed storage
rejects — forcing a bespoke two-step stage-then-commit write path
(`ohdp_ingestion.iceberg.commit`) and hand-rolled schema-evolution/upsert logic
to stand in for what dlt and dbt do natively against a plain SQL destination.

None of that complexity was buying isolation from Snowflake or portability to
another catalog — Horizon Catalog is Snowflake-specific either way. And at the
point this decision was made, the project was still at M0: three dbt models
total, no curated data published anywhere, and the DuckDB-replica/Cube/Superset
serving layer described in ADR-0002 was design-only, never implemented. There
was nothing riding on Iceberg's format-level benefits (time travel, engine
portability) that the project was actually using yet.

## Decision

Write plain native Snowflake tables instead of Iceberg tables, for the whole
pipeline — raw ingestion through marts, not just the layer that was failing:

- **Raw layer**: dlt writes straight to its built-in `snowflake` destination in
  prod (`dlt.destinations.snowflake`), or a local DuckDB file in dev/CI
  (`dlt.destinations.duckdb`) — chosen by
  `ohdp_shared.settings.is_snowflake_configured()`. No more filesystem staging,
  no more `commit()`: dlt's own write-disposition and schema-evolution handling
  do what the bespoke code used to.
- **clean/core/marts**: dbt runs against Snowflake directly in prod
  (`dbt-snowflake`, `--target prod`), and against DuckDB in local dev/CI
  (`dbt-duckdb`, `--target local`/`--target ci`) exactly as before — fully
  offline, no live Snowflake credentials needed for `make test`/`dagster dev`.
  The custom `ohdp_ingestion.dbt.iceberg` write plugin is gone; models are
  plain `materialized="table"` (full rebuild, matching the old `overwrite`
  behavior) or `materialized="incremental", incremental_strategy="merge"` (the
  one model that used `iceberg_strategy="upsert"`).
- **Credentials**: Snowflake SERVICE users don't accept password auth (nor,
  it turns out, a PAT passed as one — that only worked for Horizon's
  REST/OAuth2 exchange, a different code path from the plain SQL/JDBC/Python
  connector login dlt and dbt-snowflake need). Terraform generates an RSA key
  pair (`tls_private_key.pipeline`) and sets the public half on
  `snowflake_service_user.pipeline.rsa_public_key`; the private half (PKCS#8
  PEM) is what both dlt and dbt-snowflake authenticate with. The PAT resource
  (`snowflake_user_programmatic_access_token.pipeline`) is removed — nothing
  in this design does an OAuth2 exchange anymore, so it had no remaining job.
- **Naming**: the Iceberg-branded Terraform outputs, GitHub secret and k8s
  secret key are renamed to plain Snowflake naming
  (`SNOWFLAKE_ICEBERG_CREDENTIAL` → `SNOWFLAKE_PIPELINE_PRIVATE_KEY`, etc.) —
  no stale "iceberg" vestiges in a codebase that no longer touches Iceberg.

## Consequences

- **Deleted**: `ohdp_ingestion/iceberg.py`, `ohdp_ingestion/dbt/iceberg.py` (the
  custom dbt-duckdb write plugin), the `pyiceberg`/`dlt[pyiceberg]` dependency,
  and `data/tests/test_iceberg_lake.py`. The one piece of surviving logic —
  `namespace()`, pure string naming with no storage dependency — moved to
  `ohdp_ingestion/naming.py`.
- **The local/prod DuckDB-vs-Snowflake split is now the permanent
  architecture**, not a stepping stone: two SQL dialects to keep portable
  (already bit us once — `select * exclude (...)` happens to be valid on both,
  but that was luck, not design) is an accepted, ongoing cost in exchange for
  a dev loop that stays fast, free and offline.
- **The publish-and-replicate mechanism (ADR-0002) is retired.**
  `ohdp_orchestration/defs/publish/` (`WarehousePublish`, the `warehouse_snapshot`
  asset that built a `warehouse-{ts}.duckdb` snapshot for a DuckDB read
  replica) is deleted. It was already "defined, not wired" — no curated data
  existed to publish, and the replica Deployment it targeted was never built
  (no Helm chart for it exists). Its whole reason to exist — curated data
  living behind Snowflake auth that Cube/Superset couldn't reach directly —
  evaporates once curated data is plain Snowflake tables: Cube has a native
  Snowflake driver and can query it directly, which is strictly simpler than
  Iceberg → snapshot → replica pod → Cube reads the replica.
- **Existing Iceberg-catalog data is orphaned** — same consequence ADR-0011
  already accepted for the Polaris → Horizon move. At M0 there is nothing
  curated to lose; re-running the raw loads against the new native tables is
  the whole migration path.
- **Grants change shape but not scope**: `CREATE ICEBERG TABLE` /
  `ICEBERG TABLES` become `CREATE TABLE` / `TABLES` in
  `platform/terraform/snowflake.tf`; the account-wide network policy stays,
  though it's no longer load-bearing the way it was for PAT issuance — kept
  because it was already proven working and costs nothing at its permissive
  default.
- **Key rotation is a Terraform operation, not a token-expiry deadline.**
  Unlike the PAT it replaces (365-day cap), the RSA key pair has no expiry;
  rotate on demand with `terraform apply -replace=tls_private_key.pipeline`,
  then re-publish the `SNOWFLAKE_PIPELINE_PRIVATE_KEY` secret and redeploy.
