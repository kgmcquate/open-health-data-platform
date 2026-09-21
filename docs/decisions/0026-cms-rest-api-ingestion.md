# 0026 — CMS ingestion over its own REST API, not Socrata; the Iceberg landing side pulled out and shared

**Status:** Accepted
**Relates to:** narrows [ADR-0018](0018-socrata-ingestion-shared-across-domains.md)'s
guess about CMS; reuses the write side of
[ADR-0008](0008-config-driven-healthdata-gov-ingestion.md)/[ADR-0019](0019-iceberg-on-s3-duckdb-dbt.md).

## Context

ADR-0018's closing note guessed CMS would be "any other Socrata/CKAN catalog"
and could reuse `DatasetConfig` and the Socrata component directly. It isn't
either. Socrata's own domain-discovery API (`api.us.socrata.com/api/catalog/v1`)
404s `"Domain not found"` for `data.cms.gov`, `www.cms.gov` and
`data.medicaid.gov` — none of them are Socrata deployments. CMS publishes its
own `data-api/v1` REST API instead (documented at `data.cms.gov/api-docs`):
`GET /dataset/{uuid}/data`, offset/`size` pagination (5,000-row page cap), a
bare JSON array per page, no SoQL, no `:id`/`:updated_at` system columns.

Discovery works the same way federal open-data sites generally do: CMS
publishes a Project Open Data / DCAT-US catalog at `data.cms.gov/data.json`
(the schema every `.gov` agency is required to expose, and not coincidentally
the same vocabulary Socrata's own `Common-Core_*` catalog metadata is drawn
from — CMS's `accrualPeriodicity` and Socrata's `Common-Core_Update-Frequency`
are literally the same ISO-8601 recurring-interval codes, so the cadence
mapping is unchanged). One catalog entry is a dataset *series* — e.g.
"Medicare Part D Spending by Drug" — republished on its own cadence, each
vintage its own `distribution` with its own UUID; only the one tagged
`"description": "latest"` and `"format": "API"` is live data. 131 of CMS's
~160 series currently have one.

## Decision

**CMS gets its own `dlt` source; the Iceberg landing side moves to a shared
module both sources build on.**

1. **`ohdp_ingestion.iceberg_destination`** is
   `ohdp_ingestion/socrata/source.py`'s Horizon-Iceberg writer
   (`HorizonIcebergConfiguration`, the hand-rolled `WithStateSync` client,
   `configure_catalog`, `iceberg_catalog_config`, `build_pipeline`,
   `write_disposition`), pulled out unchanged. None of it ever depended on
   Socrata — it only cares about *where rows land* (an Iceberg table in
   `RAW.<source>` via Horizon), never *how they're fetched*. `socrata/source.py`
   now imports from it instead of defining it; `socrata_source()` (the SODA
   client) is all that's left there.

2. **`ohdp_ingestion.cms`** is CMS's own package, the same shape as
   `ohdp_ingestion.socrata` one level down (a `catalog.py` walking
   `data.json`, a `config.py` `DatasetConfig`, a `source.py` `dlt` source) —
   but its `source.py` is a thin binding of `dlt`'s own `rest_api_source`
   (`OffsetPaginator`, `total_path=None`, `stop_after_empty_page=True` since
   the `/data` response has no envelope to read a total from) rather than a
   hand-rolled client, because `data-api/v1` needs nothing bespoke the way
   Socrata's SoQL/type-serialization quirks did.

3. **`ohdp_orchestration.defs.cms.component.CMSDataset`** produces the
   identical three-asset shape `SocrataDataset` does
   (`sources/cms/<raw_table>` → `ingestion/cms/<raw_table>` →
   `lakehouse/raw/cms/<raw_table>`), but isn't a subclass of it. There is
   exactly one CMS domain, so the base/subclass split ADR-0018 introduced for
   *multiple* Socrata domains (`HealthDataGovDataset`/`CDCDataset` binding a
   shared `SocrataDataset`) has nothing to buy here — `CMSDataset` just is
   the component, duplicating the asset-spec shape in full rather than
   inventing an abstraction with one implementer.

4. **`ohdp_orchestration.jobs.cadence`** (renamed from `jobs.socrata`, whose
   `cadence_job` only ever used `source`/`title` off the `SocrataDomain` it
   took) is the one piece of orchestration-side code actually shared as-is —
   CDC, HealthData.gov and CMS all build their three cadence jobs through it.

5. **Always a full replace, never incremental.** CMS's API has no per-row
   cursor — a distribution is a whole-dataset snapshot on its own
   `accrualPeriodicity`, not an appended change log — so
   `write_disposition="replace"` unconditionally, and the clean layer
   (`macros/cms_current_rows.sql`) has no dedupe step: RAW is already the
   current snapshot, unlike `socrata_current_rows`'s `qualify` over an
   append-only history.

6. **`data/scripts/scrape_cms.py`** mirrors `scrape_socrata.py`'s shape
   (multi-document `defs.yaml`, `_PRESERVE` fields across re-runs, a
   generated dbt sources file) without sharing its code — the catalog and API
   underneath are unrelated, so there'd be nothing but the file-writing
   boilerplate to share, and that's a few dozen lines, not the ~250-line
   Iceberg writer above.

7. **`--top-n` ranks by row count**, CMS's nearest thing to Socrata's page
   views, and it is a poor proxy: it surfaces CMS's largest
   provider/claims-line-item files, not useful ones. The initial 5 enabled
   datasets (Medicare Part D/Part B/Medicaid drug spending, Medicare
   Geographic Variation, Medicare Monthly Enrollment — feeding the
   **Financial** consumer-aligned domain) were chosen by hand, the same way
   CDC's 16 were.

## Consequences

- **Existing Socrata pipeline state references the old destination path.**
  dlt's pipeline-state comparison logs (doesn't fail on) a mismatch between
  the destination class's dotted path recorded in prior runs'
  `_dlt_pipeline_state` (`ohdp_ingestion.socrata.source._destination...`) and
  the new one (`ohdp_ingestion.iceberg_destination.horizon_iceberg_destination...`).
  Confirmed harmless — `dlt/pipeline/pipeline.py::_state_to_props` only
  compares and ignores, never raises — but it will appear once per pipeline
  in logs on the first run after this deploys.
- **CMS lands in `RAW.CMS`/`CLEAN.STG_CMS`** — a Terraform change
  (`lakehouse_sources` in `variables.tf` gains `"cms"`) and an apply, same as
  any new source under ADR-0021.
- **No `CURATED.FINANCIAL` mart yet.** Picking a grain and measures across
  five differently-shaped spending/enrollment datasets is a modeling decision
  independent of wiring up ingestion, and is left for later — the same
  staged rollout ADR-0018 did for CDC (machinery + enabled set first, core
  conforming second).
- **No app token.** CMS's `data-api/v1` has no auth at all (confirmed against
  its own docs) — one fewer secret than Socrata sources need.
- **Every CMS column lands as text.** `data-api/v1` JSON-encodes every field
  as a string, and unlike Socrata's catalog there's no per-column type
  schema to hint dlt's normalizer with (no `ColumnSpec` equivalent) — typing
  is deferred entirely to `core`, whenever the curated mart is built.
