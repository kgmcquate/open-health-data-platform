# HealthData.gov ingestion

Config-driven ingestion for the [HealthData.gov](https://healthdata.gov/browse)
Socrata catalog. See [ADR-0008](../../../../../docs/decisions/0008-config-driven-healthdata-gov-ingestion.md)
for the shape and [ADR-0018](../../../../../docs/decisions/0018-socrata-ingestion-shared-across-domains.md)
for the move to a shared core, now also serving [CDC](../cdc/README.md).

```
component.py                     HealthDataGovDataset — a 3-line bind of SocrataDataset
datasets/defs.yaml               every HealthDataGovDataset instance, one `---` document each (generated)
```

All of them live in one file, one YAML document per dataset with `---` between:
Dagster's component loader reads a multi-document `defs.yaml` natively and gives
each document its own component node, so "go to definition" still lands on the
right line. A dataset's identity comes from its `attributes` (`raw_table` + the
domain), never from the path.

The class that does the work is
[`components/socrata.py`](../../components/socrata.py); `component.py` only
binds `ohdp_ingestion.healthdata_gov.HEALTHDATA_GOV`. The three cadence jobs +
schedules aren't a component (no per-instance config) — they're plain code in
[`jobs/healthdata_gov.py`](../../jobs/healthdata_gov.py) and
[`schedules/healthdata_gov.py`](../../schedules/healthdata_gov.py).

**One component instance per dataset.** Each document in `datasets/defs.yaml`
is a `HealthDataGovDataset` instance; its `attributes` block is the
`DatasetConfig` contract. The component builds that dataset's asset(s) — it
never reads the other documents.

## Regenerate the dataset instances

```bash
cd data && uv run python scripts/scrape_socrata.py --domain healthdata.gov --top-n 8
```

Walks the Socrata catalog API, rewrites `datasets/defs.yaml` whole (including
every column schema) and regenerates
`data/dbt/models/raw/_stg_healthdata_gov__sources.yml`. Datasets that left the
catalog simply stop being emitted, so there is nothing to prune. Safe to
re-run: your edits to `enabled`, `cadence`, `row_limit` and
`incremental_cursor` in an instance are preserved.

## Enable a dataset

Find the dataset's document in `datasets/defs.yaml` (search for its `raw_table`
or its 4x4 `id`) and edit it in place:

```yaml
attributes:
  enabled: true
  cadence: weekly        # daily | weekly | monthly -> which schedule picks it up
  incremental_cursor: socrata_updated_at   # null => full replace every run
  row_limit: 500000
```

Then `cd data && uv run dagster definitions validate -m ohdp_orchestration.definitions`.

## What each instance builds

Three assets per dataset, so lineage is explicit:

| | key | group | kinds | materialized | built when |
|---|---|---|---|---|---|
| **source asset** | `sources/healthdata_gov/<raw_table>` | `sources_healthdata_gov` | `socrata` | never | always |
| **table asset** | `ingestion/healthdata_gov/<raw_table>` | `ingestion_healthdata_gov` | `dlt`, `snowflake` | yes | `enabled: true` |
| **raw-layer asset** | `snowflake/raw/healthdata_gov/<raw_table>` | `snowflake_raw` | `snowflake` | runless event | `enabled: true` |

`source -> ingestion -> snowflake/raw -> (dbt clean → core → marts)`. The
raw-layer asset never runs an op of its own; the ingestion op reports a runless
materialization against it for every table the load actually touched, including
the `<raw_table>__<nested>` children dlt splits out of nested JSON.
dlt **appends** to `RAW.healthdata_gov.<raw_table>`
(ADR-0013) — full history, schema auto-evolves; `incremental_cursor: null`
datasets `replace` instead. The catalog asset carries the Socrata column
schema + publisher / URL / keywords / cadence / page-views metadata for the
OpenMetadata Dagster ingestion; the table asset gets dlt's load ids and rows
loaded after a run.

> **Check the row count before keeping `row_limit: 500000`.** The cap truncates
> an `:id`-ordered scan, and the incremental cursor then advances past the rows
> that were never fetched — so the missing tail is never backfilled.

## Schedules

[`jobs/healthdata_gov.py`](../../jobs/healthdata_gov.py) builds **exactly
three** asset jobs — `healthdata_gov_{daily,weekly,monthly}_ingest` — each
selecting table assets by their `domain` + `cadence` tags (via the shared
[`jobs/socrata.py`](../../jobs/socrata.py)).
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
  [ADR-0012](../../../../../docs/decisions/0012-native-snowflake-tables.md)
  and [ADR-0014](../../../../../docs/decisions/0014-snowflake-only-compilation.md).
- Optional `OHDP_HEALTHDATA_APP_TOKEN` raises Socrata rate limits.
- Schedules fire at 07:00 UTC, before the 08:00 dbt build.
