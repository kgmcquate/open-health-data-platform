# CMS (data.cms.gov) ingestion

Config-driven ingestion for CMS's [data.cms.gov](https://data.cms.gov) catalog
— 131 dataset series with a live API distribution, 5 enabled. See
[ADR-0026](../../../../../docs/decisions/0026-cms-rest-api-ingestion.md) for
why this isn't a Socrata binding like
[CDC](../cdc/README.md)/[HealthData.gov](../healthdata_gov/README.md), even
though it produces the identical three-asset shape.

```
component.py                     CMSDataset — the whole component (no shared base; only one CMS domain)
datasets/defs.yaml               every CMSDataset instance, one `---` document each (generated)
```

Unlike CDC/HealthData.gov, `component.py` isn't a subclass of a shared
`SocrataDataset` base — there's no second CMS-like domain to justify the
split ADR-0018 made for Socrata, so `CMSDataset` just *is* the component. It
lands its rows through the same shared destination Socrata sources use
(`ohdp_ingestion.iceberg_destination`, pulled out of
[`socrata/source.py`](../../../ohdp_ingestion/socrata/source.py) by
ADR-0026), fed by a `dlt` source built from `dlt`'s own `rest_api_source`
([`ohdp_ingestion/cms/source.py`](../../../ohdp_ingestion/cms/source.py))
rather than Socrata's hand-rolled SoQL client — CMS's API needs nothing
bespoke, just an offset paginator.

## Regenerate the dataset instances

```bash
cd data && uv run python scripts/scrape_cms.py --top-n 5
```

Walks `data.cms.gov/data.json`, rewrites `datasets/defs.yaml` whole and
regenerates `data/dbt/models/raw/_stg_cms__sources.yml`. Datasets that lose
their live API distribution simply stop being emitted. Safe to re-run: your
edits to `enabled`, `cadence` and `row_limit` in an instance are preserved.

**`--top-n` ranks by row count, not relevance** — CMS's catalog carries no
popularity signal the way Socrata's page views do, and row count is a poor
proxy too (it surfaces CMS's largest provider/claims-line-item files, not
useful starter datasets). The actual enabled set below was chosen by hand,
the same way CDC's 16 were (ADR-0018) — re-running the scraper with a
`--top-n` that doesn't match the current enabled set will just leave your
hand edits alone (`enabled` is preserved), it won't re-rank them.

## What each instance builds

Three assets per dataset, matching Socrata sources' shape exactly:

| | key | group | kinds | materialized | built when |
|---|---|---|---|---|---|
| **source asset** | `sources/cms/<raw_table>` | `sources_cms` | `cms` | never | always |
| **table asset** | `ingestion/cms/<raw_table>` | `ingestion_cms` | `dlt`, `filesystem` | yes | `enabled: true` |
| **raw-layer asset** | `lakehouse/raw/cms/<raw_table>` | `lakehouse_raw` | `iceberg` | runless event | `enabled: true` |

`source -> ingestion -> lakehouse/raw/* -> (dbt clean)`. dlt **replaces**
`RAW.CMS.<raw_table>` on every run — always, never append. CMS's
`data-api/v1` has no per-row `:id`/`:updated_at` the way Socrata's SODA API
does: a distribution is a whole-dataset snapshot republished on its own
cadence (`accrualPeriodicity`), not an appended change log, so there is no
row-level cursor to fetch against or dedupe on downstream.

## The enabled set

5 datasets, all landing in the `monthly` cadence bucket (every one of their
`accrualPeriodicity` values — annual or monthly — maps there; see
`ohdp_ingestion.cms.catalog._CADENCE_BY_FREQUENCY`), chosen for the
**Financial** consumer-aligned domain and kept small/fast on purpose:

| dataset | rows | why |
|---|---|---|
| Medicare Part D Spending by Drug | ~14.5k | drug spending, Part D |
| Medicare Part B Spending by Drug | ~0.8k | drug spending, Part B |
| Medicaid Spending by Drug | ~18.5k | drug spending, Medicaid |
| Medicare Geographic Variation — by National, State & County | ~37k | spending/utilization by geography |
| Medicare Monthly Enrollment | ~580k | beneficiary counts by geography and coverage type |

Every other CMS dataset with a live API distribution (126 of them) is
catalogued disabled — visible to OpenMetadata's Dagster ingestion, not
ingested. Enabling one is the same as CDC:

```yaml
attributes:
  enabled: true
  cadence: monthly        # daily | weekly | monthly -> which schedule picks it up
  row_limit: 1000000      # see below
```

Then `cd data && uv run dagster definitions validate -m ohdp_orchestration.definitions`.

> **Check the row count before keeping `row_limit`.** Unlike Socrata's
> `:id`-ordered scan, CMS's offset paginator caps by *pages requested*, not
> rows returned, so the true cutoff can overshoot the configured limit by up
> to one page. `curl 'https://data.cms.gov/data-api/v1/dataset/<uuid>/data-viewer/stats'`
> gives the live `total_rows` — the `row_count` field on each instance is
> only a snapshot from scrape time.

## Downstream models

No `CURATED.FINANCIAL` mart yet — that's the natural next step once the
enabled set (or a hand-picked subset of it) has a settled grain and measures,
but it's a separate design decision from wiring up ingestion, so it's left
for later. Today:

```
RAW.CMS.<table>                      dlt (this component)
  -> CLEAN.STG_CMS.<name>            models/clean/stg_cms/     -- 5 models, one per dataset
```

The clean models are all one shared macro (`macros/cms_current_rows.sql`) over
one raw table — unlike `socrata_current_rows`, there's no dedupe: RAW is
already a full replace, not an appended history, so "clean" means only
dropping dlt's bookkeeping columns and fixing column case.

## Notes

- No app token, no auth at all — CMS's `data-api/v1` is fully open (confirmed
  against its own API docs at https://data.cms.gov/api-docs).
- Every column arrives as text (CMS's API serializes every field as a JSON
  string, numbers included, the same as Socrata's SODA API). Each instance
  does carry a `columns` list, but unlike Socrata's it is documentation, not
  a typing hint: nothing feeds it to dlt's normalizer the way
  `_SOCRATA_TO_DLT_TYPE` does, because the types in it describe CMS's
  published CSV rather than what lands in `RAW`. Typing stays `core`'s job
  once a curated mart exists.
- **Where `columns` comes from.** `data.json` has no schema, so the scraper
  reads two more CMS surfaces (see `ohdp_ingestion.cms.catalog`): each
  dataset's `data-viewer` meta block gives the column names and CSV types,
  and its **data dictionary page** — a Drupal node under
  `data.cms.gov/jsonapi`, found from the catalog's `describedBy` or, failing
  that, by title — gives the descriptions. 105 of the 131 series have such a
  page; the rest document themselves in a PDF or XLSX only and get names
  without descriptions. Pass `--no-column-docs` to skip the dictionary pass,
  or `--no-details` to skip columns and row counts entirely.
- `row_count` on each instance is informational only (from the same
  `data-viewer` meta block at scrape time) — never used to drive ingestion,
  only to help pick what to enable and set a sane `row_limit`.
