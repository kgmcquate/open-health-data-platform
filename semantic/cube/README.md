# Cube Core — semantic layer

One definition per metric. Three consumers (Superset, chatbot, alerting) read
from here and must never disagree (ARCHITECTURE.md §1.5).

- `cube.js` — config: `queryRewrite` for tier limits + hard safety caps.
- `model/` — cubes: measures, dimensions, joins. Each cube reads a **dbt mart**,
  never a raw or staging table.

## Local

```bash
docker run -p 4000:4000 \
  -v "$PWD:/cube/conf" \
  -e CUBEJS_DEV_MODE=true \
  -e CUBEJS_DB_TYPE=duckdb \
  -e CUBEJS_DB_DUCKDB_DATABASE_PATH=/data/warehouse.duckdb \
  cubejs/cube:latest
```

Point it at a **copy** of the warehouse snapshot (read replica pattern, §3) —
never the file the dbt build is writing.

## Catalog sync

`catalog/openmetadata/sync` turns the cubes here into OpenMetadata Metric
entities so the chatbot's discovery MCP can see them.
