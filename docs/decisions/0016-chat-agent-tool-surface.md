# 0016 — Chat agent tool surface: our own Cube tools, OpenMetadata MCP for context

**Status:** Accepted
**Amends:** [ADR-0003](0003-cube-core-over-dbt-semantic-layer.md) and ARCHITECTURE.md §6,
both of which assume the agent reaches data through *Cube's* MCP endpoint.

## Context

ARCHITECTURE.md §6 specifies the agent has exactly two MCP connections — OpenMetadata for
discovery, Cube for execution — and no direct database access. ADR-0003 chose Cube Core partly
because it "exposes a REST/SQL/MCP interface."

That is wrong on one point. **Cube's MCP server is a Cube Cloud Premium/Enterprise feature.**
Cube Core, which is what we self-host, ships no MCP server. Cube Cloud is out of budget
(ARCHITECTURE.md §1.3, ~$35/month ceiling).

Cube Core does expose the same bounded surface over REST: `/cubejs-api/v1/meta` enumerates
every cube, measure and dimension; `/cubejs-api/v1/load` takes a **JSON query object**, not
SQL; `/cubejs-api/v1/sql` returns the compiled SQL for provenance. The property §6 actually
depends on — that the agent selects from a bounded set of measures and dimensions and can never
express a raw query — is a property of Cube's query model, not of MCP as a transport.

On the other side, OpenMetadata 2.0's MCP server *is* open source, is enabled by default, and
is live on our deployed 2.0.1. Its context tooling (`get_asset_context`, `get_persona_context`,
`find_context`, `get_knowledge_content`) is purpose-built for exactly the grounding job we would
otherwise have hand-rolled as RAG over dbt docs.

Three options were considered for the execution side:

- **Adopt a community Cube MCP server.** Least code. Rejected: an unvetted third-party
  dependency sitting in the primary safety control, with a tool surface we do not choose.
- **Buy Cube Cloud Premium.** Removes the build and adds Analytics Chat and workbooks.
  Rejected on budget (§1.3).
- **Write our own tool layer over Cube Core's REST API.** Chosen.

## Decision

**The agent's execution tools are ours, written over Cube Core's REST API** — `list_metrics`,
`describe_metric`, `run_metric_query`, `explain_query` — living in a new `ohdp_agent` package
and invoked by the tool-use loop in `hub-api`. `run_metric_query` accepts a Pydantic-validated
Cube query object and nothing else; there is no field through which a SQL string can reach Cube.

**Context and discovery come from OpenMetadata's MCP server**, consumed by an MCP client running
inside `hub-api` — not via the Anthropic API's server-side MCP connector, which would place tool
calls outside the logging and quota path §6 requires.

**A third tool group, Europe PMC literature search, is in scope from M3**, with the rule that
the agent may cite only identifiers a tool returned in the current conversation, checked in
post-processing.

**Snowflake stays out of the agent's reach entirely.** ARCHITECTURE.md §9 holds: no raw SQL
tool, and no read-only-role escape hatch either. A question Cube cannot express becomes a
modelling request, not a query.

## Consequences

- ARCHITECTURE.md §6's "two MCP connections" is now one MCP connection (OpenMetadata) plus two
  local tool groups (Cube, literature). The safety model is unchanged; the transport is not.
- We own the tool layer, which is the point: the tool surface **is** the safety control, and
  the three-place cap enforcement (tool layer → Cube `queryRewrite` → Snowflake statement
  timeout) requires code we control at the first hop.
- The OM-Metric ↔ Cube-measure mapping produced by `catalog/openmetadata/sync/cube_metrics.py`
  becomes load-bearing: it is what joins discovery to execution. It needs a CI test that every
  synced Metric round-trips to a measure present in `/v1/meta`. Drift here fails quietly.
- Catalog content reaches the prompt, so OpenMetadata descriptions and Context Center articles
  are untrusted input. Any write-back to the catalog by the agent must stay human-reviewed,
  or the injection surface becomes a cycle.
- If Cube Core ever ships an OSS MCP server, this decision is cheap to revisit — the tool
  contracts stay, only their transport changes.

See [docs/chatbot.md](../chatbot.md) for the full design this decision sits inside.
