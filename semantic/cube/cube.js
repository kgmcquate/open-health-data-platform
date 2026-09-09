// Cube Core configuration.
//
// The semantic layer is the single definition of every metric (ARCHITECTURE.md §1.5).
// Dashboards, chatbot, and alerting all read from here and must never disagree.
//
// `queryRewrite` enforces tier limits and hard safety caps on EVERY query,
// including ones the chat agent builds via MCP (§6).

module.exports = {
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

  // No Cube Store, no pre-aggregations initially (§4 resource budget).
  scheduledRefreshTimer: false,
};
