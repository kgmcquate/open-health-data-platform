# Cube Core — semantic layer

One definition per metric. Two consumers (chatbot, alerting) read
from here and must never disagree (ARCHITECTURE.md §1.5).

Cube's data source is **MotherDuck** (ADR-0029), not Snowflake. Snowflake is
still the Iceberg catalog (ADR-0019): MotherDuck has Horizon's `CURATED`
catalog attached server-side as a read-only database of the same name
(`platform/scripts/motherduck_bootstrap.py`), so the cube models'
`"CURATED"."<SCHEMA>"."<TABLE>"` resolves there unchanged and no Snowflake
warehouse runs for Cube.

Every cube below has a `pre_aggregations:` block (`type: originalSql`). Cube
persists those in the **source database**, not in Cube Store: tables in
MotherDuck's `cache.prod_pre_aggregations`, which queries then read on
MotherDuck. Cube Store (`platform/helm/charts/cubestore`, ADR-0024) holds
Cube's result cache and refresh queue, not pre-aggregated data. `scheduledRefreshTimer` in `cube.js` keeps them warm
hourly; each still inherits its cube's own `refresh_key` for on-demand
invalidation. Locally, `CUBEJS_DEV_MODE=true` auto-spawns an embedded Cube
Store, so `make cube-dev` below already exercises this — no separate local
Cube Store setup needed.

- `cube.js` — config: `queryRewrite` for tier limits + hard safety caps, and
  `MotherDuckSessionDriver`, which opens a new MotherDuck session every 45
  minutes and re-creates the `CURATED` attach from it, because the attach's
  Horizon token expires after an hour.
- `model/` — cubes: measures, dimensions, joins. Each cube reads a **dbt mart**,
  never a raw or staging table.
- `model/globals.py` — loads the dbt manifest and exposes `dbt_model()` /
  `dbt_models()` to every Jinja-templated `.yml` cube below it (cube-dbt).
- `requirements.txt` — `cube_dbt`, pip-installed on container startup (Cube's
  own convention for Python/Jinja data-model deps).

## dbt integration (cube-dbt)

Dimensions and their descriptions are **generated from dbt's column docs**,
not hand-typed twice. A cube like `model/respiratory_hospital_load.yml` does:

```yaml
{% set model = dbt_model('mart_respiratory_hospital_load') %}

cubes:
  - {{ model.as_cube() }}
    dimensions:
      {{ model.as_dimensions(skip=['avg_covid_inpatients', 'avg_occupancy']) }}
    measures:
      - name: avg_occupancy
        sql: avg_occupancy
        type: avg
        description: "{{ model.column('avg_occupancy').description }}"
```

Edit column descriptions in the dbt model's `_schema.yml` (e.g.
`data/dbt/models/marts/respiratory/_mart_respiratory__models.yml`); they flow
into Cube on the next reload. **Measures are still hand-authored in Cube** —
cube-dbt only reads dbt's documented columns, dbt has no concept of an
aggregation, so `AVG(...)` vs `SUM(...)` is defined exactly once, in the Cube
model, same as before.

`globals.py` loads the manifest with `filter(paths=['marts/'])`, which is
ADR-0003's "every cube reads a mart, never staging/raw" enforced at load time:
`dbt_model('some_staging_model')` returns `None` and the Jinja render fails
loudly instead of quietly reading the wrong table.

## Local

Cube queries MotherDuck, which reads the lakehouse through the attached
`CURATED` catalog. Export a `MOTHERDUCK_TOKEN` for a workspace that has it
attached. Local Cube doesn't get the Snowflake variables, so it doesn't
re-create the attach itself. If the attach is more than an hour old (401 from
`/v1/config`), run `make motherduck-bootstrap ARGS=--recreate` with
`OHDP_SNOWFLAKE_PAT`, `OHDP_SNOWFLAKE_ACCOUNT` and `OHDP_SNOWFLAKE_ROLE` set. `make cube-dev`
runs `dbt parse` first (no warehouse needed — it just compiles the manifest
cube-dbt reads) and mounts `data/dbt/target/` read-only into the container at
`dbt/`:

```bash
make cube-dev
```

Equivalent by hand:

```bash
cd data/dbt && uv run dbt deps && uv run dbt parse && cd -
docker run -p 4000:4000 \
  -v "$PWD:/cube/conf" \
  -v "$PWD/../../data/dbt/target:/cube/conf/dbt:ro" \
  -e CUBEJS_DEV_MODE=true \
  -e CUBEJS_API_SECRET=$OHDP_CUBE_API_SECRET \
  -e CUBEJS_DB_TYPE=duckdb \
  -e CUBEJS_DB_DUCKDB_DATABASE_PATH=md:cache \
  -e motherduck_token=$MOTHERDUCK_TOKEN \
  cubejs/cube:v1.7.46
```

`OHDP_CUBE_API_SECRET` must be exported in your shell before either form —
pick any value (e.g. `export OHDP_CUBE_API_SECRET=$(openssl rand -hex 32)`)
and put the same value in `data/.env`'s `OHDP_CUBE_API_SECRET`, since that's
what any local caller — `catalog/openmetadata/sync`'s Cube ingestion
(`data/src/ohdp_orchestration/assets/cube_metrics_sync.py`), hub-api — signs
its Cube API JWT with. In the deployed cluster this is `cube-secret`
(`platform/helm/README.md`'s secrets table), generated once and never
hand-picked.

Re-run `make dbt-parse` (or `make cube-dev` again) after editing dbt column
docs — Cube doesn't watch `data/dbt/target/manifest.json` for changes.

## Catalog sync

`catalog/openmetadata/sync` turns the cubes here into OpenMetadata Metric
entities so the chatbot's discovery MCP can see them.
