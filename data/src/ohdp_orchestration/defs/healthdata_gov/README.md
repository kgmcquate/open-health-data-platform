# HealthData.gov ingestion

Config-driven ingestion for the [HealthData.gov](https://healthdata.gov/browse)
Socrata catalog. See [ADR-0008](../../../../../../docs/decisions/0008-config-driven-healthdata-gov-ingestion.md).

```
component.py                     HealthDataGovDataset + HealthDataGovCadenceSchedules
schedules/defs.yaml              the one schedules instance (cron strings)
datasets/<slug>/defs.yaml        one HealthDataGovDataset instance per dataset (generated)
```

**One component instance per dataset.** Each `datasets/<slug>/defs.yaml` is a
`HealthDataGovDataset` instance; its `attributes` block is the `DatasetConfig`
contract. The component builds that dataset's asset(s) — it never reads the
other datasets' files.

## Regenerate the dataset instances

```bash
cd data && uv run python scripts/scrape_healthdata_gov.py --top-n 8
```

Walks the Socrata catalog API, (re)writes every `datasets/<slug>/defs.yaml`
(including the column schema), prunes dirs no longer in the catalog, ensures
`schedules/defs.yaml` exists, and regenerates
`data/dbt/models/staging/_healthdata_gov__sources.yml`. Safe to re-run: your
edits to `enabled`, `cadence`, `row_limit` and `incremental_cursor` in an
instance are preserved.

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
| **catalog asset** | `healthdata_gov/catalog/<raw_table>` | `healthdata_gov_catalog` | `socrata` | never | always |
| **table asset** | `healthdata_gov/<raw_table>` | `healthdata_gov` | `dlt`, `iceberg` | yes | `enabled: true` |

The table asset is `deps=[catalog asset]` → `catalog -> raw Iceberg table ->
(dbt clean → core → marts)`. dlt **appends** to the Iceberg table
`raw_healthdata_gov.<raw_table>` (ADR-0010) — full history, schema auto-evolves;
`incremental_cursor: null` datasets `replace` instead. The catalog asset carries
the Socrata column schema + publisher / URL / keywords / cadence / page-views
metadata for the OpenMetadata Dagster ingestion; the table asset gets the
dlt-inferred schema and Iceberg snapshot id / row count after a run.

## Schedules

`schedules/defs.yaml` (a single `HealthDataGovCadenceSchedules` instance) builds
**exactly three** asset jobs + schedules —
`healthdata_gov_{daily,weekly,monthly}_ingest` — each selecting table assets by
their `ohdp/cadence` tag. It never reads the dataset files, so adding datasets
never touches it. A cadence with no enabled datasets yet gets an empty job (its
scheduled run is a no-op until a dataset of that cadence is enabled). Schedules
are created **stopped** (`default_status` in `schedules/defs.yaml`).

## Notes

- `socrata_id` / `socrata_updated_at` are Socrata's own `:id` / `:updated_at`,
  aliased in; the `clean` layer dedupes on `socrata_id` and orders by
  `socrata_updated_at`.
- The `columns` block is Socrata's *advertised* schema; dlt infers the real
  loaded types at materialization.
- Local dev / CI use a pyiceberg SqlCatalog (SQLite) — set
  `OHDP_ICEBERG_LOCAL_*`; prod uses Polaris via `OHDP_ICEBERG_CATALOG_URI`.
  See [ADR-0010](../../../../../../docs/decisions/0010-iceberg-medallion-lakehouse.md).
- Optional `OHDP_HEALTHDATA_APP_TOKEN` raises Socrata rate limits.
- Schedules fire at 07:00 UTC, before the 08:00 dbt build.
