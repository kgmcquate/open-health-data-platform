# Cube Core — semantic layer

One definition per metric. Three consumers (Streamlit, chatbot, alerting) read
from here and must never disagree (ARCHITECTURE.md §1.5).

- `cube.js` — config: `queryRewrite` for tier limits + hard safety caps.
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

Cube queries Snowflake directly (ADR-0012/0014 — there is no local/offline
warehouse target to point it at instead). `make cube-dev` runs `dbt parse`
first (no warehouse needed — it just compiles the manifest cube-dbt reads)
and mounts `data/dbt/target/` read-only into the container at `dbt/`:

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
  -e CUBEJS_DB_TYPE=snowflake \
  -e CUBEJS_DB_SNOWFLAKE_ACCOUNT=$OHDP_SNOWFLAKE_ACCOUNT \
  -e CUBEJS_DB_SNOWFLAKE_USER=$OHDP_SNOWFLAKE_USER \
  -e CUBEJS_DB_SNOWFLAKE_PRIVATE_KEY=$OHDP_SNOWFLAKE_PRIVATE_KEY \
  -e CUBEJS_DB_SNOWFLAKE_ROLE=$OHDP_SNOWFLAKE_ROLE \
  -e CUBEJS_DB_SNOWFLAKE_WAREHOUSE=$OHDP_SNOWFLAKE_WAREHOUSE \
  cubejs/cube:latest
```

Re-run `make dbt-parse` (or `make cube-dev` again) after editing dbt column
docs — Cube doesn't watch `data/dbt/target/manifest.json` for changes.

## Catalog sync

`catalog/openmetadata/sync` turns the cubes here into OpenMetadata Metric
entities so the chatbot's discovery MCP can see them.
