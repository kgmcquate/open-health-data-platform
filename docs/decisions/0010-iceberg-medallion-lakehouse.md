# 0010 — Iceberg medallion lakehouse on Spaces, Polaris catalog

**Status:** Accepted, but the catalog and storage decisions are **superseded by
[ADR-0011](0011-snowflake-horizon-catalog.md)** — the catalog is Snowflake's
Horizon Catalog and the table files live in Snowflake-managed storage, not
Spaces. The medallion layers, the namespace scheme, the dbt-duckdb writer plugin
and the publish step below all still hold. The "Catalog: Apache Polaris (REST)"
section and the `raw`-via-dlt mechanics are history; read them for why, not for
what runs.
**Relates to:** supersedes the storage layout in
[ADR-0008](0008-config-driven-healthdata-gov-ingestion.md); amends
[ADR-0002](0002-publish-and-replicate-duckdb.md) (serving unchanged, its *inputs*
move to the lake).

## Context

ADR-0008 landed HealthData.gov tables directly in the writer DuckDB file. That
does not give a place to keep raw history, a schema-evolution story for ~150
auto-scraped datasets whose columns drift, or an organizing structure as more
sources arrive. dbt was a flat `staging / intermediate / marts`.

## Decision

A three-layer lakehouse on DigitalOcean Spaces, every table **Apache Iceberg**,
tracked by an **Apache Polaris** REST catalog.

### Layers and organization

| layer | Iceberg namespace | written by | disposition |
|---|---|---|---|
| `raw` | `raw_<source>` (`raw_healthdata_gov`) | dlt (`filesystem` + `table_format=iceberg`) | **append-only** history; `replace` for wholesale-rewritten datasets |
| `clean` | `clean_<source>` | dbt (`ohdp_ingestion.dbt.iceberg` plugin) | `overwrite`, or `upsert` for storage-incremental models |
| `curated` | `core`, `mart_<name>` | dbt | `overwrite` / `upsert` |

`raw` and `clean` are partitioned by source (one namespace each); `curated`
splits into a single conformed `core` namespace and one `mart_<name>` per mart.
All under `s3://<OHDP_SPACES_BUCKET>/`.

### Catalog: Apache Polaris (REST)

A Polaris service (`platform/helm/values/polaris.yaml`), backed by a `polaris`
database in the shared Postgres. Chosen over a pyiceberg Postgres `SqlCatalog`
because a REST catalog is attachable natively by DuckDB, Spark and Trino, has
real RBAC and credential handling, and is the emerging standard. Cost: ~0.5–1 GB
RAM (JVM) + one Postgres DB — noted against ARCHITECTURE §4's budget.

`ohdp_ingestion.iceberg.load_catalog()` is the single entry point. In local dev
and CI (`OHDP_ICEBERG_CATALOG_URI` unset) it falls back to a pyiceberg
`SqlCatalog` on SQLite with a local warehouse dir — same API, no services.

The pipeline suppresses pyiceberg's default `X-Iceberg-Access-Delegation:
vended-credentials` header and passes `s3.*` to pyiceberg itself: Polaris is
given no storage credentials (it cannot subscope for non-AWS Spaces), so asking
it to vend only earns a `403` on the `*_WITH_WRITE_DELEGATION` op.

The Apache Polaris **console** (a browser SPA) is deployed for catalog
inspection/admin. Upstream (`apache/polaris-tools`) ships neither a Helm repo nor
an image, so the chart is vendored (`platform/helm/vendor/polaris-console`, see
its `VENDORED.md`) and the image is built from the same pinned commit by
`build-polaris-console.yml`.

It is served at `https://polaris.ohdp.kevinmcquate.com` behind a **second
oauth2-proxy** (`oauth2-proxy-polaris`) — a Google login wall with an *email
allowlist* (unlike the any-Google-account Dagster wall, since the console exposes
the catalog admin API). That same proxy reverse-proxies `/api/*` on the host to
the Polaris Service, so the SPA and the catalog API share one origin (no CORS)
and the whole surface sits behind the wall; Polaris keeps no Ingress of its own.

Auth into Polaris is unchanged — `authentication.type: internal`. The console
logs in with Client Credentials as the `ohdp` root principal. Per-user identity
in the catalog would need Polaris `mixed` auth against a real IdP that issues
role claims (Auth0/Keycloak — not Google); deferred until there is a second user.

### Raw is append-only, dedupe in `clean`

dlt fetches only changed rows (SoQL `$where` on `:updated_at`) but **appends** —
raw is the full history of what the API returned, and dlt evolves the Iceberg
schema (`union_by_name`) on every append. The `clean` model reduces to the
current row per `socrata_id`. `incremental` behaviour above raw is
**storage-level**: the model computes its full output and the plugin `upsert`s,
so re-runs only rewrite changed rows. Compute-incremental (`is_incremental()`)
would need a custom materialization — deferred.

### dbt writes Iceberg via a custom plugin

dbt-duckdb's built-in `iceberg` plugin is read-only and `external` only writes
Parquet. `ohdp_ingestion.dbt.iceberg` adds `store()` (pyiceberg
create / evolve / `overwrite` / `upsert`) and `load()`. Models are
`materialized: external, plugin: iceberg`; dbt writes a scratch Parquet to
`external_root` (local) and the plugin commits it to the catalog. The Iceberg
table is the durable artifact — same-run `ref()`s read the scratch copy (same
data); the publish step and external readers go through the catalog.

### dbt models are Dagster assets under `warehouse/`

`ohdp_orchestration.defs.warehouse` (a `dbt_assets` component) surfaces the dbt
project as assets keyed `warehouse/<model>`, grouped `warehouse_<layer>`, kinds
`dbt` + `iceberg`. A translator maps dbt **sources** back to the keys the
ingestion components already own (`healthdata_gov/<raw_table>`), so the graph is
one connected line: `catalog → raw (dlt) → warehouse/stg_… → core → mart`. `dbt
build` runs inside the asset, so a failed test fails the asset (§3). Needs
`dbt/target/manifest.json` — the image and CI run `dbt parse`; `pytest` bootstraps
it (conftest); `dagster dev` self-heals.

### Serving (ADR-0002 preserved)

A publish step reads the `curated` namespaces via the catalog, assembles
`warehouse-{ts}.duckdb`, uploads it and updates `current.json`; the replica pulls
the snapshot as before and never touches Iceberg. Iceberg snapshot history adds
per-table time travel on top of whole-snapshot rollback. *(Defined but not wired
to a replica — M0 curated data does not exist yet.)*

## Consequences

- New deps in `data/`: `dlt[pyiceberg,s3,filesystem]`, `pyiceberg[s3fs,sql-sqlite]`,
  `sqlalchemy`. New settings: `OHDP_ICEBERG_*`, `OHDP_SPACES_REGION`,
  `OHDP_DBT_STAGE_DIR`.
- New infra: Polaris service + `polaris` Postgres DB + a bootstrap (catalog
  `ohdp`, a pipeline principal). `platform/helm/values/polaris.yaml`,
  `postgres.databases += polaris`.
- Polaris console: a vendored chart (`vendor/polaris-console`), a GHCR image
  built off-cycle (`build-polaris-console.yml`), and a second `oauth2-proxy`
  release at `polaris.ohdp.kevinmcquate.com` (new DNS record + a redirect URI on
  the existing Google OAuth client). ~50 MB against §4's budget.
- dbt must run in the project venv (`cd data && uv run dbt`) so the plugin
  module is importable — `uvx --from dbt-duckdb dbt` will not work. CI updates.
- The M0 `openaq` / `cdc` sources (ADR-0008 left them as `main`-schema stubs) are
  removed from dbt; they will re-enter as `raw_openaq` / `raw_cdc` Iceberg
  namespaces when those clients are built.
- Two writers still hit one logical store; `maxConcurrentRuns: 1` (§3/§4) still
  serializes them. Iceberg's per-table ACID means a failed dbt model no longer
  half-writes a table.
- ARCHITECTURE.md §2/§3/§7 diagrams predate this — a docs pass is owed.
