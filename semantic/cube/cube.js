// Cube Core configuration.
//
// The semantic layer is the single definition of every metric (ARCHITECTURE.md §1.5).
// Dashboards, chatbot, and alerting all read from here and must never disagree.
//
// `queryRewrite` enforces tier limits and hard safety caps on EVERY query,
// including ones the chat agent builds via MCP (§6).

const { DuckDBDriver } = require('@cubejs-backend/duckdb-driver');

// DuckDBDriver.query() runs every query on ONE shared connection
// (defaultConnection.all), and node-duckdb executes each call on the libuv
// threadpool. Two overlapping queries against the attached Iceberg catalog
// corrupt each other's binding ("INTERNAL Error: Attempted to dereference
// unique_ptr that is NULL", "Referenced column ... not found. Candidate
// bindings: "__""), for queries that succeed when run alone.
//
// CUBEJS_CONCURRENCY=1 (platform/helm/charts/cube/values.yaml) doesn't prevent
// it: OptsHandler.queueOptionsWrapper applies it per *queue*, and the query
// queue and the pre-aggregation queue each get their own 1 — so a live query
// and a scheduledRefreshTimer refresh-key/pre-agg query still overlap on that
// connection. Serialise at the driver instead, the one place every query
// passes through. stream() (pre-agg loads) opens its own connection and is
// left alone.
class SerializedDuckDBDriver extends DuckDBDriver {
  constructor(config) {
    super(config);
    this.queryChain = Promise.resolve();
  }

  query(query, values, options) {
    const run = this.queryChain.then(() => super.query(query, values, options));
    this.queryChain = run.catch(() => {});
    return run;
  }
}

module.exports = {
  // Returning an instance keeps CUBEJS_DB_TYPE=duckdb as the dialect
  // (OptsHandler.getDbType); Cube caches one driver per data source.
  driverFactory: ({ dataSource }) => new SerializedDuckDBDriver({ dataSource }),

  queryRewrite: (query, { securityContext }) => {
    const tier = (securityContext && securityContext.tier) || 'free';

    // Hard safety caps — apply regardless of tier.
    const MAX_ROWS = 10000;
    query.limit = Math.min(query.limit || MAX_ROWS, MAX_ROWS);

    // Tier-based row ceiling (provisional — ARCHITECTURE.md §10).
    if (tier === 'free') {
      query.limit = Math.min(query.limit, 1000);
    }

    return query;
  },

  // Cube Store pre-aggregations (ADR-0024): refresh hourly. Source datasets
  // update at daily/weekly cadence at best, so hourly keeps rollups fresh
  // enough for the chatbot/MCP path without meaningful refresh load.
  scheduledRefreshTimer: 60 * 60,
};
