# 0030 — The data API is a Plus feature, served by one gateway in hub-api

**Status:** Accepted

## Context

Three things were worth selling access to from outside the hub: Cube's REST
API, the Cube MCP server, and OpenMetadata's MCP server. Before this change,
none of them was actually sold:

- Cube had a public Ingress, but only a JWT signed with the internal
  `CUBEJS_API_SECRET` was accepted. In practice, only hub-api could call it.
- `mcp-cube` was ClusterIP-only, with one shared bearer token and a hardcoded
  `free` tier (ADR-0017's server, kept for the chat agent).
- OpenMetadata's `/mcp` was public for anyone with an OpenMetadata token,
  with no tier check and no limit.

hub-api already owns everything a paywall needs: the `users.tier` column, the
Stripe webhook that sets it (`hub_api.billing`), and per-tier quotas counted
from Postgres rows (`hub_api.db`).

## Decision

**One gateway, `/v1/...` on hub-api, fronts every paid surface, and nothing
behind it is public.**

- `hub_api.api_keys` issues personal API keys (`ohdp_…`). Only a SHA-256 of
  each key is stored. Keys are created on the Developer page (`/developer`),
  and only a Plus user can create one.
- On every call, the key's owner's tier is read from `users`, not from the
  session cookie. A cancellation from the Stripe webhook therefore cuts access
  at the next call instead of at the next login.
- `hub_api.api_usage` counts calls from an `api_usage` log, per calendar month
  in UTC. There are two pools:
  - Cube queries: `/v1/cube/load` and `/v1/cube/sql`.
  - MCP tool calls: a `tools/call` on either MCP endpoint.
  
  `/v1/cube/meta` and MCP protocol traffic (`initialize`, `tools/list`) are
  free. Over the allowance, a call is refused with 429, or with a JSON-RPC
  error for a single MCP call so the model can explain it. **There is no
  overage billing yet.**
- The Cube routes accept only a `CubeQuery`, the same bounded model the chat
  agent uses (ARCHITECTURE.md §6). The Cube service token is minted with the
  caller's live tier, so `queryRewrite` applies the Plus row cap.
- `/v1/mcp/cube` runs `ohdp_mcp`'s tools in-process, in stateless mode. The
  caller's tier reaches the tools through the ASGI scope, which the MCP
  transport hands to each tool as its request. A ContextVar wouldn't cross into
  the task a tool runs in.
- `/v1/mcp/catalog` proxies to OpenMetadata's `/mcp` in the cluster. It sends
  a **separate read-only bot token** (`OHDP_OPENMETADATA_GATEWAY_JWT`,
  DataConsumer role) and never the caller's key. The chat agent's
  `OHDP_OPENMETADATA_JWT` is not used, because it can write (ADR-0027).
- Cube's Ingress is removed. On the catalog host, an Ingress in the hub-api
  chart claims `/mcp` and rewrites it to `/v1/mcp/catalog`. Traefik ranks the
  longer rule first, so OpenMetadata's UI and REST API on `/` are untouched
  and stay free.

In-app chat doesn't use the gateway. It keeps its daily quotas and its
cluster-internal `mcp-cube` and OpenMetadata connections, and it isn't metered.

## Alternatives considered

- **Metering at each service** (Cube's `checkAuth` calling back to hub-api,
  an OpenMetadata plugin): three enforcement points to keep in step, and
  OpenMetadata has no hook for it.
- **Stripe usage-based billing (Billing Meters)**: deferred. With no overage,
  a hard cap needs no Stripe integration. Adding a metered price later only
  needs each `api_usage` row reported as a meter event.
- **An HTTP SQL gateway to MotherDuck**: rejected. It wouldn't work like a
  MotherDuck connection (DuckDB CLI, `md:` in Python, DBeaver), which is the
  point of offering SQL. SQL access is deferred. The likely design is a
  MotherDuck service account and read-scaling token per Plus user (MotherDuck's
  Admin API), plus a restricted share of a native-MotherDuck copy of the
  curated tables. MotherDuck has no quota API, so limits there would be soft
  (Duckling size and monitoring).

## Consequences

- hub-api is now on the path of every paid call, so an outage of hub-api is an
  outage of the API. It already was one for the hub.
- The catalog host has two certificates, one per namespace, because a TLS
  secret must live beside its Ingress.
- Existing OpenMetadata MCP clients, including this repo's `.mcp.json`, now
  need an OHDP API key from a Plus account instead of an OpenMetadata token.
- OpenMetadata's REST API is still reachable with a user's own OpenMetadata
  token, because the UI needs it. Only its MCP endpoint is paywalled.
- The check-then-insert quota can overrun by a few calls under concurrency,
  the same trade the chat quota makes.
- Allowances (`OHDP_PLUS_MONTHLY_CUBE_QUERIES`, `OHDP_PLUS_MONTHLY_MCP_CALLS`)
  are provisional. `apps/web/src/pages/Billing.tsx` repeats them in the plan
  copy, so change both together.
