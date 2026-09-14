# CDC (data.cdc.gov) ingestion

Config-driven ingestion for the [data.cdc.gov](https://data.cdc.gov/browse)
Socrata catalog — 1,077 datasets, 16 enabled. The machinery is shared with
[HealthData.gov](../healthdata_gov/README.md); see
[ADR-0018](../../../../../docs/decisions/0018-socrata-ingestion-shared-across-domains.md)
for why, and [ADR-0008](../../../../../docs/decisions/0008-config-driven-healthdata-gov-ingestion.md)
for the original shape.

```
component.py                     CDCDataset — a 3-line bind of SocrataDataset
datasets/defs.yaml               every CDCDataset instance, one `---` document each (generated)
```

All of them live in one file, one YAML document per dataset with `---` between:
Dagster's component loader reads a multi-document `defs.yaml` natively and gives
each document its own component node, so "go to definition" still lands on the
right line. A dataset's identity comes from its `attributes` (`raw_table` + the
domain), never from the path.

The class that does the work is
[`components/socrata.py`](../../components/socrata.py); `component.py` only
binds `ohdp_ingestion.cdc.CDC`. The three cadence jobs + schedules aren't
components (no per-instance config) — they're plain code in
[`jobs/cdc.py`](../../jobs/cdc.py) and
[`schedules/cdc.py`](../../schedules/cdc.py).

## Regenerate the dataset instances

```bash
cd data && uv run python scripts/scrape_socrata.py --domain data.cdc.gov --top-n 8
```

Walks the Socrata catalog API, rewrites `datasets/defs.yaml` whole (including
every column schema) and regenerates
`data/dbt/models/raw/_stg_cdc__sources.yml`. Datasets that left the catalog
simply stop being emitted, so there is nothing to prune. Safe to re-run: your
edits to `enabled`, `cadence`, `row_limit` and `incremental_cursor` in an
instance are preserved.

## What each instance builds

Three assets per dataset, so lineage is explicit:

| | key | group | kinds | materialized | built when |
|---|---|---|---|---|---|
| **source asset** | `sources/cdc/<raw_table>` | `sources_cdc` | `socrata` | never | always |
| **table asset** | `ingestion/cdc/<raw_table>` | `ingestion_cdc` | `dlt`, `filesystem` | yes | `enabled: true` |
| **raw-layer asset** | `lakehouse/raw/cdc/<raw_table>` | `lakehouse_raw` | `iceberg` | runless event | `enabled: true` |

`source -> ingestion -> lakehouse/raw/* -> (dbt clean → curated)`. The raw-layer
asset never runs an op of its own; the ingestion op reports a runless
materialization against it for every table the load actually touched, including
the `<raw_table>__<nested>` children dlt splits out of nested JSON.

dlt **appends** to `RAW.CDC.<raw_table>` (ADR-0013) — full history, schema
auto-evolves; `incremental_cursor: null` datasets `replace` instead.

## The enabled set

16 datasets, chosen by hand rather than by `--top-n`: page-view rank is an
all-time counter and puts archived 2020-21 COVID datasets above everything
current. The first eight are live surveillance; the rest track the health
topics [cdc.gov](https://www.cdc.gov/) features on its front page.

**Respiratory & notifiable disease surveillance**

| dataset | id | cadence | rows |
|---|---|---|---|
| NNDSS Weekly Data | `x9gk-5huc` | weekly | ~2.0M |
| NWSS Public SARS-CoV-2 Wastewater Metric Data | `2ew6-ywp6` | weekly | ~840k |
| Wastewater Viral Activity (SARS-CoV-2, Flu A, RSV) | `atcp-73re` | weekly | ~560k |
| NSSP ED Visit Trajectories by State | `rdmq-nq56` | weekly | ~660k |
| RSV-NET Rates and Clinical Data | `29hc-w46k` | weekly | ~510k |
| Weekly US Hospitalization Metrics by Jurisdiction | `aemt-mg7g` | weekly | ~13k |
| Influenza Vaccination Coverage, All Ages | `vh55-3he6` | monthly | ~240k |
| Vaccination Coverage, 0-35 Months | `fhky-rtsk` | monthly | ~140k |

**CDC front-page topics**

| topic on cdc.gov | dataset | id | cadence | rows |
|---|---|---|---|---|
| Measles (2025 outbreaks) | CDC Wastewater Data for Measles | `akvg-8vrb` | weekly | ~73k |
| H5 Bird Flu | CDC Wastewater Data for Avian Influenza A (H5) | `mtpu-urpp` | weekly | ~121k |
| Mental health | NSSP Mental Health-Related ED Visit Rates | `eze9-ahe5` | weekly | ~11k |
| Overdose Prevention | VSRR Provisional Drug Overdose Death Counts | `xkb8-kh2a` | monthly | ~86k |
| Healthy Weight | Nutrition, Physical Activity and Obesity (BRFSS) | `hn4x-zwk7` | monthly | ~111k |
| Preventing Chronic Diseases | U.S. Chronic Disease Indicators | `hksd-2xuw` | monthly | ~399k |
| Alzheimer's Disease | Alzheimer's Disease and Healthy Aging Data | `hfr9-rurv` | monthly | ~284k |
| Diabetes / High Blood Pressure | PLACES: County Data, 2025 release | `swc5-untb` | monthly | ~229k |

Diabetes and high blood pressure have no strong standalone dataset on
data.cdc.gov — the live ones (USDSS) are low-traffic slices. Both are measures
*within* Chronic Disease Indicators and PLACES, which is why those two carry
the topic instead.

## Downstream models

Every enabled dataset has a clean model; the curated layer and Cube sit on top.

```
RAW.CDC.<table>                      dlt (this component)
  -> CLEAN.STG_CDC.<name>            models/clean/stg_cdc/     -- 16 models, one per dataset
  -> CURATED.CORE.<fact>             models/curated/core/      -- 10 conformed facts
  -> CURATED.<MART>.<table>          models/curated/<mart>/    -- chronic_disease, infectious_disease,
                                                                  respiratory, behavioral_health, immunization
  -> Cube                            semantic/cube/model/      -- measures per mart
```

The clean models are all one shared macro (`macros/socrata_current_rows.sql`)
over one raw table: latest row per Socrata `:id`, dlt bookkeeping dropped.
Adding a dataset means adding one four-line model.

The one non-obvious piece of the core layer is **`core__health_indicator`**:
Chronic Disease Indicators, PLACES, the BRFSS nutrition/activity/obesity table
and the Alzheimer's table are four dialects of a single shape (location, period,
category, measure, one `data_value` with its own type and unit, a CI, an
optional demographic stratum). Conforming them into one long fact is what lets a
single mart and a single cube serve every chronic-disease topic on cdc.gov's
front page, instead of four near-identical stacks. The four disagree on nearly
every column name -- CDI drops the underscores (`datavalue`,
`lowconfidencelimit`), PLACES says `category`/`measure` where the others say
`class`/`question` -- so the mapping is the whole model.

**`data_value_type` is never filtered away in core.** CDC publishes the same
measure as both crude and age-adjusted prevalence, and averaging the two
together is meaningless, so it stays a dimension and each Cube measure filters
to one type.

## Enable another dataset

Find the dataset's document in `datasets/defs.yaml` (search for its `raw_table`
or its 4x4 `id`) and edit it in place:

```yaml
attributes:
  enabled: true
  cadence: weekly        # daily | weekly | monthly -> which schedule picks it up
  incremental_cursor: socrata_updated_at   # null => full replace every run
  row_limit: null        # see below
```

Then `cd data && uv run dagster definitions validate -m ohdp_orchestration.definitions`.

> **Check the row count before keeping `row_limit: 500000`.** The cap truncates
> an `:id`-ordered scan, and the incremental cursor then advances past the rows
> that were never fetched — so the missing tail is never backfilled. For
> anything near or above the cap, set `row_limit: null`.
> `curl 'https://data.cdc.gov/resource/<id>.json?$select=count(*)'` tells you.

## Notes

- `socrata_id` / `socrata_updated_at` are Socrata's own `:id` / `:updated_at`,
  aliased in; a `clean` layer would dedupe on `socrata_id` and order by
  `socrata_updated_at`.
- The `columns` block is Socrata's *advertised* schema; dlt infers the real
  loaded types at materialization.
- ~30 CDC datasets are named for a year, so their `raw_table` starts with `_` —
  dlt's own rule for a leading digit, applied by `config.table_name()` so the
  declared name matches the physical one. The directory keeps the plain slug.
- Optional `OHDP_CDC_APP_TOKEN` raises Socrata rate limits; the API is readable
  without one.
- Schedules fire at 06:00 UTC, ahead of HealthData.gov's 07:00 and the 08:00
  dbt build.
