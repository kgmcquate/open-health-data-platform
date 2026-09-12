# Cube Core — semantic layer

One definition per metric. Three consumers (Streamlit, chatbot, alerting) read
from here and must never disagree (ARCHITECTURE.md §1.5).

- `cube.js` — config: `queryRewrite` for tier limits + hard safety caps.
- `model/` — cubes: measures, dimensions, joins. Each cube reads a **dbt mart**,
  never a raw or staging table.

## Local

Cube queries Snowflake directly (ADR-0012/0014 — there is no local/offline
warehouse target to point it at instead):

```bash
docker run -p 4000:4000 \
  -v "$PWD:/cube/conf" \
  -e CUBEJS_DEV_MODE=true \
  -e CUBEJS_DB_TYPE=snowflake \
  -e CUBEJS_DB_SNOWFLAKE_ACCOUNT=$OHDP_SNOWFLAKE_ACCOUNT \
  -e CUBEJS_DB_SNOWFLAKE_USER=$OHDP_SNOWFLAKE_USER \
  -e CUBEJS_DB_SNOWFLAKE_PRIVATE_KEY=$OHDP_SNOWFLAKE_PRIVATE_KEY \
  -e CUBEJS_DB_SNOWFLAKE_ROLE=$OHDP_SNOWFLAKE_ROLE \
  -e CUBEJS_DB_SNOWFLAKE_WAREHOUSE=$OHDP_SNOWFLAKE_WAREHOUSE \
  cubejs/cube:latest
```

## Catalog sync

`catalog/openmetadata/sync` turns the cubes here into OpenMetadata Metric
entities so the chatbot's discovery MCP can see them.
