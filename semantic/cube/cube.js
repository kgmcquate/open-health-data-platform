// Cube Core configuration.
//
// The semantic layer is the single definition of every metric (ARCHITECTURE.md §1.5).
// Dashboards, chatbot, and alerting all read from here and must never disagree.
//
// `queryRewrite` enforces tier limits and hard safety caps on EVERY query,
// including ones the chat agent builds via MCP (§6).

const { finished } = require('stream/promises');
const { DuckDBDriver } = require('@cubejs-backend/duckdb-driver');
const { DuckDBRowStream } = require('@cubejs-backend/duckdb-driver/dist/src/RowStream.js');
const {
  buildTransform,
  convertDuckDBParams,
  transformChunk,
} = require('@cubejs-backend/duckdb-driver/dist/src/Transform.js');

// MotherDuck's server-side attach of Horizon's CURATED catalog (ADR-0029)
// authenticates with the OAuth access token the `ohdp_horizon` secret held
// when the database was created, not with the PAT. Horizon issues those
// tokens for an hour, and nothing refreshes them: an hour after the attach,
// every read of CURATED fails with a 401 from /v1/config. Replacing the
// secret alone doesn't help, because the database keeps the old token. The
// database has to be dropped and created again, and a MotherDuck session
// already open keeps the attach it saw when it connected, so the new attach
// only reaches a new session.
//
// So Cube opens a new MotherDuck session (a new DuckDBInstance) well inside
// that hour. The new session re-creates the secret, which gets a fresh
// token, and then CURATED from it, before it serves any query. That takes
// 25-30s, so it happens in the background. The current session keeps
// serving, because re-creating CURATED from another session doesn't change
// its attach, until the new one is ready. The session it replaces stays open
// until the queries leased from it finish.
//
// This can be removed once DuckDB refreshes the token itself. The iceberg
// extension does have OAuth2 refresh: it gets a new token before expires_in
// runs out, and retries once after a 401. That arrived in DuckDB 1.5:
// https://github.com/duckdb/duckdb-iceberg/commit/9c0a7fb8f730139e44f29fe66b8ece33aa6d55e1
// But 1.5 only treats a secret as refreshable if it has a refresh_token or a
// non-empty client_id, and Horizon requires CLIENT_ID '' and returns no
// refresh_token. This commit (2026-09-09, on main, not on the v1.5 branch)
// accepts an empty client_id:
// https://github.com/duckdb/duckdb-iceberg/commit/1d80755f9d554936d0a5d722a8fe81350934ab9b
// Once a DuckDB release includes it and MotherDuck supports that release for
// its server-side attach, test it: leave CURATED attached for more than an
// hour without re-creating it. If reads still work, delete this driver and go
// back to the stock DuckDBDriver.
//
// After ROTATE_AFTER, start opening the next session.
const SESSION_ROTATE_AFTER_MS = 40 * 60 * 1000;
// After MAX_AGE, the token is close to expiring, so queries wait for the next
// session instead (only if the background open is late or failed).
const SESSION_MAX_AGE_MS = 55 * 60 * 1000;

const sqlStr = (value) => `'${String(value).replace(/'/g, "''")}'`;

// Keep in step with platform/scripts/motherduck_bootstrap.py, which creates
// the same objects for a fresh workspace.
function refreshCuratedStatements() {
  const {
    OHDP_SNOWFLAKE_PAT: pat,
    OHDP_SNOWFLAKE_ACCOUNT: account,
    OHDP_SNOWFLAKE_ROLE: role,
  } = process.env;
  // `make cube-dev` passes no Snowflake credentials. It uses whatever attach
  // the workspace already has.
  if (!pat || !account || !role) return [];
  const endpoint = `https://${account}.snowflakecomputing.com/polaris/api/catalog`;
  return [
    `CREATE OR REPLACE SECRET ohdp_horizon IN MOTHERDUCK (
      TYPE ICEBERG,
      CLIENT_ID '',
      CLIENT_SECRET ${sqlStr(pat)},
      OAUTH2_SERVER_URI ${sqlStr(`${endpoint}/v1/oauth/tokens`)},
      OAUTH2_SCOPE ${sqlStr(`session:role:${role}`)}
    )`,
    'DROP DATABASE IF EXISTS CURATED',
    `CREATE DATABASE CURATED (
      TYPE ICEBERG,
      "secret" ohdp_horizon,
      endpoint ${sqlStr(endpoint)},
      warehouse 'CURATED',
      default_schema 'CORE',
      access_delegation_mode 'vended_credentials',
      read_only true
    )`,
  ];
}

// Every query and stream runs on a connection of its own, taken from the
// current session. That also covers the overlap problem the old
// single-connection driver had: concurrent queries on DuckDB's one shared
// connection corrupted each other's binding.
//
// Replaces the parts of DuckDBDriver (1.7.46) that use its single
// initPromise/defaultConnection: query(), stream(), release(). init() is
// reused unchanged to open each session.
class MotherDuckSessionDriver extends DuckDBDriver {
  constructor(config) {
    super(config);
    // The session queries lease from. Always one that finished opening.
    this.session = null;
    // The session being opened to replace it, if any.
    this.next = null;
  }

  async openSession() {
    const { defaultConnection, instance } = await this.init();
    let opened = false;
    try {
      for (const sql of refreshCuratedStatements()) {
        await defaultConnection.run(sql);
      }
      opened = true;
      return instance;
    } finally {
      defaultConnection.closeSync();
      if (!opened) instance.closeSync();
    }
  }

  // Starts opening the next session, if one isn't already opening. It
  // replaces the current session once it's ready.
  rotate() {
    if (this.next) return this.next;
    // openedAt is when the token is issued, so set it before the open starts.
    const next = { openedAt: Date.now(), leases: 0, retired: false };
    next.instance = this.openSession();
    this.next = next;
    next.instance.then(
      () => {
        // release() already retired it.
        if (this.next !== next) return;
        const previous = this.session;
        this.session = next;
        this.next = null;
        if (previous) this.retire(previous);
      },
      () => {
        // The next lease that needs a session tries again.
        if (this.next === next) this.next = null;
      },
    );
    return next;
  }

  // A lease keeps its session open until release() is called, even after a
  // newer session has replaced it.
  async lease() {
    let { session } = this;
    const age = session ? Date.now() - session.openedAt : Infinity;
    if (age > SESSION_ROTATE_AFTER_MS) {
      const next = this.rotate();
      if (age > SESSION_MAX_AGE_MS) session = next;
    }
    // Counted before the await, so a retire() in between can't close the
    // instance under us.
    session.leases += 1;
    try {
      const instance = await session.instance;
      return {
        instance,
        release: () => {
          session.leases -= 1;
          this.closeIfIdle(session);
        },
      };
    } catch (e) {
      session.leases -= 1;
      throw e;
    }
  }

  retire(session) {
    session.retired = true;
    this.closeIfIdle(session);
  }

  closeIfIdle(session) {
    if (session.retired && session.leases === 0) {
      session.instance.then((instance) => instance.closeSync(), () => {});
    }
  }

  async query(query, values = []) {
    const { instance, release } = await this.lease();
    let connection;
    try {
      connection = await instance.connect();
      const result = await connection.run(query, convertDuckDBParams(values));
      const transform = buildTransform(result.columnNames(), result.columnTypes());
      const rows = [];
      for (const chunk of await result.fetchAllChunks()) {
        for (const row of transformChunk(chunk, transform)) {
          rows.push(row);
        }
      }
      return rows;
    } finally {
      connection?.closeSync();
      release();
    }
  }

  async stream(query, values, { highWaterMark }) {
    const { instance, release } = await this.lease();
    let connection;
    let closed = false;
    const close = () => {
      if (!closed) {
        closed = true;
        connection?.closeSync();
        release();
      }
    };
    try {
      connection = await instance.connect();
      const result = await connection.stream(query, convertDuckDBParams(values));
      const transform = buildTransform(result.columnNames(), result.columnTypes());
      // The row stream calls close() once it is done or destroyed.
      const rowStream = new DuckDBRowStream(result, transform, close, highWaterMark);
      return {
        rowStream,
        release: async () => {
          rowStream.destroy();
          await finished(rowStream, { cleanup: true }).catch(() => {});
        },
      };
    } catch (e) {
      close();
      throw e;
    }
  }

  async release() {
    const { session, next } = this;
    this.session = null;
    this.next = null;
    if (session) this.retire(session);
    if (next) this.retire(next);
  }
}

module.exports = {
  // Returning an instance keeps CUBEJS_DB_TYPE=duckdb as the dialect
  // (OptsHandler.getDbType); Cube caches one driver per data source.
  driverFactory: ({ dataSource }) => new MotherDuckSessionDriver({ dataSource }),

  queryRewrite: (query, { securityContext }) => {
    const tier = (securityContext && securityContext.tier) || 'free';

    // Hard safety caps — apply regardless of tier.
    const MAX_ROWS = 50000;
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
