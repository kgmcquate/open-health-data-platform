# 0008 — Config-driven HealthData.gov ingestion (Dagster component + dlt)

**Status:** Accepted

## Context

ARCHITECTURE.md §8 starts the pipeline with two hand-written source clients
(OpenAQ, one CDC dataset). HealthData.gov alone exposes ~150 tabular datasets on
a single Socrata domain, all reachable through one uniform API. Writing a Python
client and wiring an asset/job/schedule per dataset does not scale, and a
schedule per dataset would flood the daemon (§5 — the UI already polls hard).

## Decision

Ingestion for a bulk catalog is **config, not code**.

1. **Scrape once.** `data/scripts/scrape_healthdata_gov.py` walks the Socrata
   catalog API and writes **one Dagster component instance per dataset** at
   `ohdp_orchestration/defs/healthdata_gov/datasets/<slug>/defs.yaml`. The
   `DatasetConfig` contract (`ohdp_ingestion.healthdata_gov.config`) is the
   schema of the instance's `attributes` block — the single schema shared by
   scraper and component. Re-running preserves human overrides (`enabled`,
   `cadence`, `row_limit`, `incremental_cursor`) and prunes dirs no longer in
   the catalog.

2. **One component class, one instance per dataset.** `HealthDataGovDataset`
   (`dagster.components.Component`, subclassing `DatasetConfig`) builds *its own*
   dataset's assets and nothing else — no loop over sibling files. A second
   component, `HealthDataGovCadenceSchedules` (one instance at
   `schedules/defs.yaml`), builds the jobs + schedules. `definitions.py`
   autoloads the `ohdp_orchestration.defs` package
   (`dagster.components.load_defs`). `load_from_defs_folder` was rejected: the
   `--no-editable` image (ADR-0004) has no project `pyproject.toml` beside the
   installed `definitions.py` for it to read. `[tool.dg]` in
   `data/pyproject.toml` is kept for `dg` CLI use only.

3. **Two assets per dataset, with lineage.** Each `HealthDataGovDataset`
   instance emits:
   - a **catalog asset** `healthdata_gov/catalog/<raw_table>` — an unexecutable
     `AssetSpec` (kind `socrata`, group `healthdata_gov_catalog`) standing for
     the dataset as published on HealthData.gov. Always. Never materialized.
   - a **table asset** `healthdata_gov/<raw_table>` — a `@multi_asset` (kinds
     `dlt` + `duckdb`, group `healthdata_gov`) whose body runs a `dlt` pipeline,
     declared `deps=[catalog asset]`. Only when `enabled: true`.

   Disabled datasets have only the catalog node. Enabling one adds the table
   node downstream without touching the catalog node. This keeps the whole
   HealthData.gov surface visible to the OpenMetadata Dagster ingestion, with an
   explicit `catalog -> landed table` edge for everything that is ingested.

4. **Rich catalog metadata.** The catalog asset carries the Socrata column
   schema as `dagster/column_schema` (`TableSchema`), plus publisher, source
   URL, keywords, cadence, page views, and `domain`/`enabled` tags. The table asset
   carries `dagster/table_name` and, after a run, the column schema `dlt`
   actually inferred and a `dagster/row_count`. `@multi_asset` over
   `@dlt_assets` on purpose: one clean key per dataset instead of dlt's
   source/destination pair.

5. **dlt does the transport.** One `dlt` source per dataset over
   `/resource/{id}.json`, landing in the `healthdata_gov` schema of the writer
   DuckDB file. `:id` / `:updated_at` are aliased in as `socrata_id` /
   `socrata_updated_at` so merge + incremental work for any dataset without
   knowing its business schema. dbt sees the tables through the generated
   `data/dbt/models/staging/_healthdata_gov__sources.yml`.

6. **Three jobs, three schedules — total.** `HealthDataGovCadenceSchedules`
   builds one asset job + schedule per `cadence` (`daily` | `weekly` |
   `monthly`), each selecting table assets by their `cadence` **tag** — so
   it needs no knowledge of the dataset instances. A cadence with nothing
   enabled yet is an empty job (its scheduled run is a no-op). Adding datasets
   never touches this component.

7. **Enabled set is small and explicit.** The scraper enables the top *N*
   datasets by page views (default 8) and writes the rest `enabled: false`.

## Consequences

- New HealthData.gov datasets: flip `enabled: true` (and check `cadence`) in
  `datasets/<slug>/defs.yaml` — no code change, no new schedule. Disabled
  datasets already appear in the Dagster catalog and flow to OpenMetadata.
- ~142 dataset directories under `datasets/`, each one `defs.yaml`. Generated
  and pruned by the scraper; a human only ever opens the one they're enabling.
- `data/` gains `dlt`, `dagster-dlt`, `pyyaml`, and a `[tool.dg]` project config.
  `dagster-dlt` pins `dagster==1.13.21` exactly; a Dagster bump now waits on a
  matching `dagster-dlt` release.
- `data/` is now a standalone `uv` project (own `uv.lock`, `ohdp-shared` via a
  path dependency). The `[tool.ruff]` / `[tool.mypy]` / `[tool.pytest]` config
  that was inherited from the monorepo root now lives in `data/pyproject.toml`.
- ~150 catalog `AssetSpec`s load on every webserver/daemon start. Cost is a YAML
  parse each; negligible so far, revisit if the catalog 10×s.
- Two asset keys per enabled dataset. dbt sources map to the table key
  (`healthdata_gov/<raw_table>`); the catalog key sits one hop upstream.
- The pattern generalizes: any other Socrata/CKAN catalog (data.cdc.gov, CMS)
  can reuse `DatasetConfig` and the component with a different scraper.
- Wholesale-rewritten datasets (Socrata replaces all rows, one `:updated_at`)
  re-pull fully each run and dedupe. Set `incremental_cursor: null` on those to
  switch the resource to `replace`.
- ARCHITECTURE.md §2/§8 diagrams still say "5 sources"; that wording is now
  understated but not wrong — left for a docs pass.
- The ingest jobs and the dbt build job all write the same `warehouse.duckdb`.
  They are kept apart only by the run launcher's `maxConcurrentRuns: 1`
  (§3/§4). Schedules fire at 07:00 so ingestion drains from the queue before
  the 08:00 build. A sensor chaining ingest → build is the eventual fix.
