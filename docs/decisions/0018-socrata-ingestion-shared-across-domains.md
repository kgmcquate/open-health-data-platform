# 0018 — One Socrata ingestion core, bound per domain (CDC)

**Status:** Accepted

## Context

[ADR-0008](0008-config-driven-healthdata-gov-ingestion.md) made HealthData.gov
ingestion config, not code: scrape the Socrata catalog once, emit one Dagster
component instance per dataset, let three cadence jobs pick up whatever is
`enabled`. Its closing note said the pattern generalizes — "any other
Socrata/CKAN catalog (data.cdc.gov, CMS) can reuse `DatasetConfig` and the
component with a different scraper."

data.cdc.gov is the next source, and it is the same software: Tyler Data &
Insights (Socrata), the same catalog API, the same `/resource/{id}.json`, the
same `:id` / `:updated_at` system columns. Copying the ~600 lines of
HealthData.gov machinery to sit beside an identical CDC copy would leave two
implementations of one protocol to keep in sync.

## Decision

**The Socrata machinery is shared; a domain is a constant.**

1. **`ohdp_ingestion.socrata`** holds everything that was HealthData.gov-shaped
   only by its string literals: the catalog walk (`catalog.py`), the
   `DatasetConfig` contract (`config.py`), and the `dlt` source + pipeline
   (`source.py`). Nothing in it names a domain.

2. **`SocrataDomain`** is the whole difference between two catalogs — host,
   `source` slug, human title, app-token env var. Each source package binds one
   and adds nothing else: `ohdp_ingestion.healthdata_gov.HEALTHDATA_GOV`,
   `ohdp_ingestion.cdc.CDC`. The `source` slug is the identity that must never
   change once data has landed: it is the `RAW` schema (ADR-0013), the middle
   segment of every asset key, the dbt source name, the `domain` tag the cadence
   jobs select on, and the `dlt` schema name in the destination's pipeline state.

3. **`ohdp_orchestration.components.socrata.SocrataDataset`** is the component
   base class, holding the asset shape ADR-0008 described. `HealthDataGovDataset`
   and `CDCDataset` are three-line subclasses that bind `SOCRATA` and nothing
   else; each `defs.yaml`'s `type:` still names its own concrete class. The base
   lives *outside* `ohdp_orchestration.defs` — that package is autoloaded, and an
   abstract class with no instances has no business being walked by the loader.

4. **Three assets per dataset.** Unchanged from ADR-0008 and now stated
   explicitly, because the middle one is the only one that runs:
   - `sources/<source>/<raw_table>` — unexecutable `AssetSpec`, kind `socrata`,
     carrying the advertised column schema and catalog metadata. Always. Never
     materialized.
   - `ingestion/<source>/<raw_table>` — the `@dlt_assets` pipeline. Only when
     `enabled`.
   - `snowflake/raw/<source>/<raw_table>` — unexecutable `AssetSpec` labelling
     the physical table, which **receives a runless materialization event** from
     the ingestion op for every table the load actually touched (including the
     `<raw_table>__<nested>` children dlt splits out of nested JSON). Only when
     `enabled`.

5. **One scraper.** `data/scripts/scrape_socrata.py --domain <host>` replaces
   `scrape_healthdata_gov.py`; the per-domain output paths and component type are
   derived from the `SocrataDomain`. Overrides (`enabled`, `cadence`,
   `row_limit`, `incremental_cursor`) are still preserved across re-runs.

6. **`jobs.socrata.cadence_job`** builds the `<source>_<cadence>_ingest` job for
   any domain; `jobs/healthdata_gov.py` and `jobs/cdc.py` are three calls each.
   CDC's schedules fire at 06:00 UTC — ahead of HealthData.gov's 07:00 and the
   08:00 dbt build, so with `maxConcurrentRuns: 1` both buckets drain first.

7. **CDC captures the whole catalog** — 1,077 dataset instances, 16 enabled.

## Two bugs this surfaced, both fixed in the shared core

Neither could fire on HealthData.gov's catalog; both would have on CDC's.

- **Dagster re-types catalog strings.** Component YAML is resolved through
  Jinja's *native* environment, which `ast.literal_eval`s whatever it renders —
  so Socrata's year tags (`"2019"`, 296 of them) arrive as `int` and the junk tag
  `"..."` as `Ellipsis`, failing `list[str]` validation. The values are quoted
  correctly and survive `yaml.safe_load`; the coercion happens after parsing, so
  quoting cannot prevent it. `config.py` now coerces catalog text back with
  `field_validator(mode="before")` — *not* an `Annotated` alias, because Dagster
  reads its component schema off these annotations and rejects any `Annotated`
  metadata that is not one of its own `Resolver`s.
- **Leading-digit table names.** ~30 CDC datasets are named for a year ("500
  Cities…", "1998-2024 Serotype Data…"). A leading digit is not a legal unquoted
  SQL identifier, and dlt silently prefixes it with `_` on the way to the
  warehouse — so `raw_table`, the generated dbt source, and the `snowflake/raw/`
  asset key would all have named a table that does not exist. `config.table_name()`
  now applies dlt's own rule. Directory names keep the plain slug: a defs
  directory starting with `_` risks being treated as private by a loader.

## Consequences

- **Code-location load goes from ~5.2s to ~13.4s** (220 → 1,313 asset keys)
  on a warm local machine. This is the "revisit if the catalog 10×s" case
  ADR-0008 anticipated, arriving as predicted. Tolerable for now — it is a
  once-per-webserver/daemon-start cost, ~7.5ms per dataset, almost all of it
  YAML parse plus pydantic validation. The lever if it stops being tolerable is
  capping the scrape by page views rather than changing the design.
- ~1,077 new dataset directories under `defs/cdc/datasets/`, one `defs.yaml`
  each. Generated and pruned by the scraper; a human only ever opens the one
  they're enabling.
- `RAW.cdc` is a new Snowflake schema, with `_stg_cdc__sources.yml` generated
  beside HealthData.gov's. The read side follows the same bind-per-domain shape:
  `macros/socrata_current_rows.sql` is the shared clean-layer body and
  `cdc_current_rows` / `healthdata_gov_current_rows` are one-line bindings of it,
  exactly as `SocrataDomain` is on the write side. `CLEAN.stg_cdc` has one model
  per enabled dataset, feeding ten `CURATED.core` facts, five marts
  (chronic_disease, infectious_disease, respiratory, behavioral_health,
  immunization) and their Cube measures.
- The CDC `enabled` set is **chosen by hand, not by page views**. Page-view
  rank is an all-time counter and ranks archived 2020-2021 COVID datasets above
  everything current; the scraper still *defaults* to `--top-n` by page views,
  and the 16 were then set by hand (the scraper preserves that across re-runs).
  Eight are live surveillance — NNDSS Weekly, two wastewater feeds, RSV-NET,
  NSSP ED visit trajectories, hospitalization metrics, two vaccination-coverage
  sets. Eight more track the topics cdc.gov features on its front page: measles
  and H5 bird flu wastewater, mental-health ED visits, provisional drug overdose
  deaths, obesity (BRFSS), Chronic Disease Indicators, Alzheimer's, and PLACES
  county data. See `defs/cdc/README.md` for the topic-to-dataset mapping.
- **`row_limit: null` on eight of the sixteen.** The cap truncates an
  `:id`-ordered scan, and because the incremental cursor then advances past rows
  that were never fetched, the missing tail is never backfilled. Anything above
  ~200k rows therefore runs uncapped, leaving headroom before a growing dataset
  crosses the 500k default (NNDSS is already ~2M). The first run across the
  enabled set is a multi-million-row backfill.
- `ohdp_ingestion.cdc.CDCSodaSource` — an M0 stub that never did anything but
  raise `NotImplementedError` — is deleted; the shared component supersedes it.
- ADRs 0008/0013/0014 refer to `scripts/scrape_healthdata_gov.py` by name. Those
  are historical records and are left as written; the script is now
  `scripts/scrape_socrata.py --domain healthdata.gov`.
