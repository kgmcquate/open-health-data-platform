# CDC (data.cdc.gov) ingestion

Config-driven ingestion for the [data.cdc.gov](https://data.cdc.gov/browse)
Socrata catalog — 1,077 datasets, 8 enabled. The machinery is shared with
[HealthData.gov](../healthdata_gov/README.md); see
[ADR-0018](../../../../../docs/decisions/0018-socrata-ingestion-shared-across-domains.md)
for why, and [ADR-0008](../../../../../docs/decisions/0008-config-driven-healthdata-gov-ingestion.md)
for the original shape.

```
component.py                     CDCDataset — a 3-line bind of SocrataDataset
datasets/<slug>/defs.yaml        one CDCDataset instance per dataset (generated)
```

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

Walks the Socrata catalog API, (re)writes every `datasets/<slug>/defs.yaml`
(including the column schema), prunes dirs no longer in the catalog, and
regenerates `data/dbt/models/raw/_stg_cdc__sources.yml`. Safe to re-run: your
edits to `enabled`, `cadence`, `row_limit` and `incremental_cursor` in an
instance are preserved.

## What each instance builds

Three assets per dataset, so lineage is explicit:

| | key | group | kinds | materialized | built when |
|---|---|---|---|---|---|
| **source asset** | `sources/cdc/<raw_table>` | `sources_cdc` | `socrata` | never | always |
| **table asset** | `ingestion/cdc/<raw_table>` | `ingestion_cdc` | `dlt`, `snowflake` | yes | `enabled: true` |
| **raw-layer asset** | `snowflake/raw/cdc/<raw_table>` | `snowflake_raw` | `snowflake` | runless event | `enabled: true` |

`source -> ingestion -> snowflake/raw -> (dbt clean → curated)`. The raw-layer
asset never runs an op of its own; the ingestion op reports a runless
materialization against it for every table the load actually touched, including
the `<raw_table>__<nested>` children dlt splits out of nested JSON.

dlt **appends** to `RAW.cdc.<raw_table>` (ADR-0013) — full history, schema
auto-evolves; `incremental_cursor: null` datasets `replace` instead.

## The enabled set

Live surveillance, chosen by hand — `--top-n` ranks by an all-time page-view
counter, which puts archived 2020-2021 COVID datasets above everything current.

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

## Enable another dataset

Edit `datasets/<slug>/defs.yaml`:

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
