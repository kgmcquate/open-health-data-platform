# HealthData.gov ingestion

Config-driven ingestion for the [HealthData.gov](https://healthdata.gov/browse)
Socrata catalog. See [ADR-0008](../../../../../../docs/decisions/0008-config-driven-healthdata-gov-ingestion.md).

```
component.py                     HealthDataGovDataset
datasets/<slug>/defs.yaml        one HealthDataGovDataset instance per dataset (generated)
```

The three cadence jobs + schedules aren't a component (no per-instance
config) — they're plain code in
[`jobs/healthdata_gov.py`](../../jobs/healthdata_gov.py) and
[`schedules/healthdata_gov.py`](../../schedules/healthdata_gov.py).

**One component instance per dataset.** Each `datasets/<slug>/defs.yaml` is a
`HealthDataGovDataset` instance; its `attributes` block is the `DatasetConfig`
contract. The component builds that dataset's asset(s) — it never reads the
other datasets' files.

## Regenerate the dataset instances

```bash
cd data && uv run python scripts/scrape_healthdata_gov.py --top-n 8
```

Walks the Socrata catalog API, (re)writes every `datasets/<slug>/defs.yaml`
(including the column schema), prunes dirs no longer in the catalog, and
regenerates
`data/dbt/models/clean/healthdata_gov/_healthdata_gov__sources.yml`. Safe to
re-run: your edits to `enabled`, `cadence`, `row_limit` and
`incremental_cursor` in an instance are preserved.

## Enable a dataset

Edit `datasets/<slug>/defs.yaml`:

```yaml
attributes:
  enabled: true
  cadence: weekly        # daily | weekly | monthly -> which schedule picks it up
  incremental_cursor: socrata_updated_at   # null => full replace every run
  row_limit: 500000
```

Then `cd data && uv run dagster definitions validate -m ohdp_orchestration.definitions`.

## What each instance builds

Two assets per dataset, so lineage is explicit:

| | key | group | kinds | materialized | built when |
|---|---|---|---|---|---|
| **catalog asset** | `sources/healthdata_gov/<raw_table>` | `sources_healthdata_gov` | `socrata` | never | always |
| **table asset** | `ingestion/healthdata_gov/<raw_table>` | `ingestion_healthdata_gov` | `dlt`, `snowflake` | yes | `enabled: true` |

The table asset is `deps=[catalog asset]` → `catalog -> raw table ->
(dbt clean → core → marts)`. dlt **appends** to `RAW.healthdata_gov.<raw_table>`
(ADR-0013) — full history, schema auto-evolves; `incremental_cursor: null`
datasets `replace` instead. The catalog asset carries the Socrata column
schema + publisher / URL / keywords / cadence / page-views metadata for the
OpenMetadata Dagster ingestion; the table asset gets dlt's load ids and rows
loaded after a run.

## Schedules

[`jobs/healthdata_gov.py`](../../jobs/healthdata_gov.py) builds **exactly
three** asset jobs — `healthdata_gov_{daily,weekly,monthly}_ingest` — each
selecting table assets by their `cadence` tag.
[`schedules/healthdata_gov.py`](../../schedules/healthdata_gov.py) wraps each
in a cron schedule. Neither reads the dataset files, so adding datasets never
touches them. A cadence with no enabled datasets yet gets an empty job (its
scheduled run is a no-op until a dataset of that cadence is enabled). Schedules
are created **stopped**; flip `default_status` in `schedules/healthdata_gov.py`
to turn them on.

## Notes

- `socrata_id` / `socrata_updated_at` are Socrata's own `:id` / `:updated_at`,
  aliased in; the `clean` layer dedupes on `socrata_id` and orders by
  `socrata_updated_at`.
- The `columns` block is Socrata's *advertised* schema; dlt infers the real
  loaded types at materialization.
- dlt writes to Snowflake directly, set via `OHDP_SNOWFLAKE_ACCOUNT` and
  friends. See
  [ADR-0012](../../../../../../docs/decisions/0012-native-snowflake-tables.md)
  and [ADR-0014](../../../../../../docs/decisions/0014-snowflake-only-compilation.md).
- Optional `OHDP_HEALTHDATA_APP_TOKEN` raises Socrata rate limits.
- Schedules fire at 07:00 UTC, before the 08:00 dbt build.
