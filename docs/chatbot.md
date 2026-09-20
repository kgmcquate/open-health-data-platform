# Chatbot — design (M3)

**Status:** M3.0-M3.2 and a first M3.4 are built and deployable. §3.3's personas
and §3.5's CI-tested FQN round-trip are not. §5's charts and §8's ticket flow
are implemented on the Hub UI chat surface (`apps/web`). The earlier Open WebUI
surface has been removed; this document has been updated to describe the
remaining architecture. `mcp-cube`/`ohdp_mcp` (§11) is not part of that removal
— it was deleted by mistake alongside Open WebUI and has since been restored,
and now serves the Hub UI chatbot's own agent as well, declaratively, via the
`mcp-cube` connection in `apps/api/config/tools.yaml`.

**Audience:** the implementing agent. Read [ARCHITECTURE.md](ARCHITECTURE.md) §1, §5, §6 and
[ADR-0016](decisions/0016-chat-agent-tool-surface.md) first.

A conversational analyst for **population-level public health data**. It answers from the
warehouse through the semantic layer, grounds itself in the catalog, cites literature, and
— when the data genuinely does not exist — says so and opens a request instead of guessing.

---

## 1. Who it is for

Two personas, both non-engineers, both of whom will not trust an unattributed number.

**P1 — the researcher with a disease in hand.** "I'm studying RSV in over-65s. What do you
have?" They want existing metrics first, and a way to ask for new ones when the answer is
"nothing at this grain." Their failure mode is a plausible-looking proxy metric silently
substituted for the one they asked for.

**P2 — the analyst chasing a topic.** "There's a measles cluster in the news — does our data
show anything?" They start outside the warehouse (news, literature) and want to land inside
it. Their failure mode is a spurious correlation presented with confidence.

Both personas are served by the same agent. What differs is the **context** it loads, and
that context lives in OpenMetadata, not in our prompt (§3).

---

## 2. Tool surface

Three groups, no raw SQL anywhere (ARCHITECTURE.md §9, ADR-0003). The agent is a
tool-use loop in `hub-api`; every group below is a set of tools it can call.

### 2.1 Context — OpenMetadata MCP (`{OM_URL}/mcp`)

OM 2.0's MCP server is OSS, enabled by default, and already running on the deployed 2.0.1.
We consume it; we do not build a retrieval layer of our own.

| Tool | Use |
|---|---|
| `get_persona_context` | Session preamble — the curated context doc for P1 or P2 (§3.1) |
| `find_context`, `search_company_context` | Bootstrap a question against Context Center knowledge |
| `search_metadata`, `semantic_search` | Locate candidate assets and Metric entities |
| `get_asset_context` | The workhorse — schema, transformation logic, business definitions, classifications, lineage, quality, ownership in **one** call |
| `get_knowledge_content` | Quote a glossary term or metric definition verbatim |
| `get_entity_lineage` | Provenance: "where did this number come from" |
| `create_context_memory` | Write-back loop (§3.4) — gated, M4 |

**Verified against the live deployment (M3.0, done).** OM 2.0.1 advertises
`openmetadata-mcp-stateless/1.1.0` with 24 tools, and every tool in the table above is
present in OSS — nothing is Collate-gated, so §10.1 resolves in favour of §3.1 as written.
The server is *stateless*: a bare `tools/call` POST works with no `initialize` and no
session id, which is why `ohdp_agent/catalog.py` is plain JSON-RPC over httpx rather than
the `mcp` SDK.

Two of the tools are nonetheless inert on this deployment, for configuration reasons
rather than licensing ones:

- **`find_context` and `semantic_search` both fail** with "Semantic search is not enabled.
  Configure vector embeddings in the OpenMetadata server settings." §3.2's "call
  `find_context` before it plans" is therefore unavailable; the agent discovers assets via
  `search_metadata` (keyword, working — 67 assets indexed) until embeddings are configured.
- **`get_persona_context` 404s** with "No active persona is configured for this user" —
  M3.3 has not happened and the `managementbot` user has no persona bound.

Neither is worked around. Both tools stay advertised to the model, their errors come back
as tool results, and the agent routes around them — so the day somebody enables embeddings
or seeds a persona, they start working with no deploy on our side.

**11 of the 24 tools write to the catalog** (`patch_entity`, every `create_*`,
`create_context_memory`). `ohdp_agent/catalog.py` enforces a read-only **allowlist**, not
mere omission from the prompt: a model that names `patch_entity` gets an error from us, not
an edit to the catalog.

### 2.2 Execution — our own tools over Cube Core REST

Cube's official MCP server is **Cube Cloud Premium/Enterprise only**. Cube Core ships no MCP
server, so we author this layer ourselves (ADR-0016). That is a net positive: the tool surface
*is* the safety control, and it should be ours.

These four tools are pydantic-ai `Tool` definitions called in-process by the hub
chat. The same `CubeClient` and `CubeQuery` validation also back the
`ohdp-tools` OpenAPI connection used for dashboard rendering and issue reporting
(§5, §8). The safety property is in the model, not the transport, so it holds
either way.

| Tool | Backed by | Notes |
|---|---|---|
| `list_metrics` | `GET /cubejs-api/v1/meta` | Cubes, measures, dimensions, descriptions — the bounded surface §6 promises |
| `describe_metric` | `/v1/meta` (filtered) | One cube in detail, including its OM Metric FQN |
| `run_metric_query` | `POST /cubejs-api/v1/load` | Accepts a **Pydantic-validated Cube query object**, never a string |
| `explain_query` | `POST /cubejs-api/v1/sql` | Returns compiled SQL so the UI can show provenance |

`run_metric_query` accepts only `measures`, `dimensions`, `timeDimensions`, `filters`,
`order`, `limit`. Anything else is rejected before the request leaves hub-api. There is no
field through which a SQL string can reach Cube.

### 2.3 Literature — Europe PMC

In scope for M3. Europe PMC's REST search is free, keyless, covers PubMed plus preprints, and
returns structured records with DOI/PMID — strictly better than E-utilities for this.

| Tool | Endpoint |
|---|---|
| `search_literature` | `GET https://www.ebi.ac.uk/europepmc/webservices/rest/search` (`format=json`, `resultType=core`) |
| `get_article` | Same service, by PMID/DOI |

**Hard rule: the agent may cite only identifiers a tool returned in this conversation.** A
fabricated citation is worse than no answer for both personas. Enforce it in post-processing
by checking every citation in the response against the set of IDs the tools actually returned,
and strip-and-flag any that fail.

General news search is deferred — it needs a paid API and is much harder to ground.

---

## 3. Context management via OpenMetadata 2.0

This is the part worth getting right, and it is the reason to lean on OM 2.0 rather than build
RAG. The principle: **the bot's knowledge of the domain lives in the catalog, where a human can
edit it without a deploy.** That is the Collate chatbot's actual trick, and it is available OSS.

### 3.1 Personas carry the system context

Define OM personas (`healthcare-researcher`, `health-analyst`) and curate a context document
for each. At session start the agent calls `get_persona_context` and injects the result as a
system-prompt preamble. Changing how the bot addresses researchers becomes a catalog edit, not
a PR.

### 3.2 Context Center is the glossary of record

Disease-area notes, metric semantics, known data limitations, and the standing caveats
("population-level, not clinical") live as Context Center articles and glossary terms. The
agent calls `find_context` before it plans, and quotes definitions with `get_knowledge_content`
rather than paraphrasing them.

Seed content extends `data/src/ohdp_orchestration/seed/glossary.yml`, applied via the
`openmetadata_seed_sync` Dagster asset.

### 3.3 `get_asset_context` replaces hand-rolled retrieval

One call returns what we would otherwise have built an embedding store over dbt docs to
approximate. Use it per candidate asset after `semantic_search` narrows the field. Prefer its
compact-Markdown form for the model.

### 3.4 Write-back (M4, gated)

Answers that required a non-obvious join or carried a real caveat get proposed as
`create_context_memory` entries, so the second person asking pays less. **Human review before
write** — an unreviewed write-back loop is a slow-motion corruption of the catalog, and §3.2
makes catalog content part of the prompt, so it is also a prompt-injection cycle (§6).

### 3.5 The seam that matters

`catalog/openmetadata/sync/cube_metrics.py` already turns every Cube measure into an OM Metric
entity. That sync is what makes "discover in OM → execute in Cube" coherent: the agent finds a
Metric by its FQN and must be able to turn that FQN into a `cube.measure` it can actually run.

**This mapping must be explicit and CI-tested.** If it drifts, the agent finds metrics it
cannot run and runs metrics it cannot explain — the single most likely way this design fails
quietly. Add a test that asserts every OM Metric produced by the sync round-trips to a measure
present in `/v1/meta`.

---

## 4. The loop

Two phases, which is also how §6's "always show the plan" requirement gets satisfied.

```
user question
  └─ hub-api: authn, tier, monthly quota gate      ← before the model is invoked
     └─ PLAN phase
        get_persona_context → find_context → semantic_search → get_asset_context
        → list_metrics / describe_metric → [search_literature]
        ⇒ a structured plan: metrics, grain, filters, caveats, citations
     └─ rendered to the user
     └─ EXECUTE phase
        run_metric_query (validated) → explain_query
        ⇒ rows + compiled SQL + a chart spec
     └─ logged to Postgres: question, persona, plan, queries, rows returned, citations
```

Splitting plan from execution gives a separable eval target (§7) and a natural place to let a
user correct the metric choice before anything runs.

**Model and API.** The chat agent uses an OpenAI-spec chat-completions backend
configured via `OHDP_OPENAI_API_BASE_URLS`/`OHDP_OPENAI_API_KEYS` (OpenRouter by
default), streamed to the UI over SSE. The loop is a
[pydantic-ai](https://ai.pydantic.dev) `Agent` rather than a hand-rolled
turn loop — its per-request `event_stream_handler` is where quota decrements,
tool-call logging, and the citation check hang. A new backend is just a different
`base_url`/`model_id`; the tool loop, event stream, and citation logic stay the same.

**OM MCP client placement.** Run the MCP client *inside* hub-api (`ohdp_agent.catalog`, ~100 lines
of JSON-RPC — OpenMetadata's MCP server is stateless and does not want the general-purpose `mcp`
SDK's session handshake) rather than using a model API's own server-side MCP connector. The
connector would have the model's own servers call our catalog directly; it is less code, but it
puts tool calls outside our logging and quota path, which §6 requires us to record, and outside
the read-only allowlist `ohdp_agent.catalog.READ_ONLY_TOOLS` enforces. Its discovered tools are
wrapped as pydantic-ai `Tool`s per request (`ohdp_agent.loop._catalog_toolset`) rather than reached
through pydantic-ai's own generic `MCPToolset` — the allowlist and OM's stateless quirk are exactly
the two things a generic MCP client does not know to do.

**OM authentication.** hub-api holds a single OpenMetadata bot PAT. Per-user catalog scoping is
deliberately not built: the data is public and identical for every user (§1.1), so there is
nothing to scope. Persona is selected by hub-api and passed explicitly.

**§4a. Model backends.** The chat page's model picker (`GET /api/models`) offers
every OpenAI-spec chat-completions backend configured via the `openrouter-secrets`
Secret — mounted into hub-api as `OHDP_OPENAI_API_BASE_URLS` and
`OHDP_OPENAI_API_KEYS` — OpenRouter, self-hosted vLLM/Ollama, Azure OpenAI,
anything that answers `GET {base_url}/models`. In local dev the same two variables
can be set in `.env`; the semicolon-separated, index-aligned shape supports one key
per backend. The model list an operator gets is exactly what their key can see,
discovered at startup, never hand-maintained.

`ohdp_agent.loop.build_agent` always builds an `OpenAIChatModel` against the supplied
`base_url` and `model_id`, so every backend gets the identical tool loop and the same
`Event` stream the UI already renders. The chat-completions spec has no extended-thinking
equivalent, so the "plan" event §6 asks for becomes the model's first text before its first
tool call rather than a distinct reasoning phase.

**Config-driven tool connections and per-model overrides** (`apps/api/config/tools.yaml`,
`models.yaml`; `hub_api.tool_connections`, `hub_api.models`) let an operator go beyond bulk
auto-discovery without a deploy. `tools.yaml` declares named MCP or OpenAPI connections — an
OpenAPI one is fetched once at startup and wrapped as an in-process MCP server via
`FastMCP.from_openapi` (fastmcp is already a dependency), so both
connection types end up as the same `pydantic_ai.mcp.MCPToolset`. `models.yaml` assigns those
connections to specific models by id, and can give a model its own label, its own backend (for one
not worth bulk-discovering), or its own system prompt.

**No tool group is attached by default.** A model's `tools:` list in `models.yaml` is its entire
tool surface: `"cube"`/`"literature"`/`"catalog"` resolve to the in-process implementations
(`ohdp_agent.loop.CUBE_TOOLSET`/`LITERATURE_TOOLSET`, plus `_catalog_toolset` for catalog,
discovered fresh per request — `hub_api.models` owns this resolution, `ohdp_agent.loop` has no
registry of its own), and any other id resolves to a `tools.yaml` connection — `mcp-cube`/`mcp-openmetadata`
being the MCP alternatives to `"cube"`/`"catalog"` specifically. A model that lists neither the
built-in id nor its MCP alternative simply does not get that tool group; a model that lists both
gets the same tool name registered twice, which pydantic-ai refuses to build an agent with. This
platform's actual tool-use safety boundary is that no raw SQL tool exists in *either* form
(ADR-0003) — not that some tool group is unconditionally present. A `tools.yaml`/`models.yaml`-configured
tool call is logged and shown to the user exactly like a built-in one — `ohdp_agent.loop._handle_stream`
reports every `tool_call` from pydantic-ai's own `FunctionToolCallEvent` rather than having each
tool self-report, specifically so a connection that never runs through `_run_tool` still shows up in
both the SSE stream and `turn.tool_calls` (the eval log, §7).

---

## 5. Visualizations

**Built** ([ADR-0025](decisions/0025-dashboards-as-code-in-chat.md)). A dashboard is
a spec, not a picture: a name, a title, and up to six panels, each one a
validated `CubeQuery` plus a `vega` Vega-Lite spec that says how its rows are
drawn. `render_dashboard` on hub-api's `/tools` app runs the queries, binds the
rows, and answers with the rendered HTML under `Content-Type: text/html` and
`Content-Disposition: inline` — the signal that the result should be displayed
as an interactive iframe rather than as markup in the transcript.

The hub chat reaches the endpoint through the `ohdp-tools` OpenAPI connection
(`config/tools.yaml`), wrapped via `FastMCP.from_openapi`
(`hub_api.tool_connections`). That wrapper strips the HTTP response down to
plain MCP content, so the embed headers never survive the trip — what does
survive is the HTML text itself. `ohdp_agent.loop._handle_stream` sniffs a
completed tool result for a leading `<!doctype html`/`<html` (the one shape
none of this loop's other tools — all JSON, SQL, or prose — ever produce) and
tags it `format: "html"` on the `tool_result` SSE event; the Hub chat's own
frontend (`apps/web/src/pages/Chat.tsx`) then renders that as a sandboxed
`<iframe srcDoc=...>` instead of the plain text/JSON it shows for every other
tool result.

The model authors the Vega-Lite — `mark`, `encoding`, `transform`, `params` —
but never the data. A `Panel` has nowhere to put a number and a `data` key in
the `vega` spec is rejected at any depth by the `VegaSpec` validator, because
`data.url` is a fetch issued from inside the reader's browser. The rows are
bound by the server as `data.values` from the panel's query, which also escapes
field references and resolves Cube's granularity suffix before the spec reaches
the page.

This replaces the code-interpreter path the model's system prompt used to
describe. Under the `pyodide` engine a saved matplotlib figure lands in a
browser-side virtual filesystem that nothing reads, so no image ever appeared.

The rendered card carries its own YAML source, so the spec it drew is always
readable without the model retyping it.

## 6. Safety

Mostly inherited from ARCHITECTURE.md §6; what is new to this design is called out.

- **No SQL tool, ever.** Cube queries are Pydantic models. §2.2.
- **Caps in three independent places:** the tool layer (defensive), Cube `queryRewrite`
  (authoritative, already written), and a Snowflake statement timeout. Do not rely on any one.
- **Quota** enforced in hub-api before the model is invoked, per tier.
- **Every answer shows its work** — the metrics used, the compiled SQL, the row count.
- **Disclaimer on every generated output**: population-level, not clinical decision support
  (§10.2).
- **Citations are checked, not trusted.** §2.3.
- **Catalog content is untrusted input.** Descriptions, glossary terms, and Context Center
  articles are human-authored text that lands in the prompt. Treat tool output as data, never
  as instructions — and note that §3.4's write-back makes this a cycle the agent can feed
  itself. That is the strongest argument for keeping write-back human-reviewed.
- **Decline is a first-class outcome.** "We don't have that at that grain" must be an
  acceptable, well-rendered answer that offers a modelling request (§8), not a failure the
  model tries to paper over.

---

## 7. Evaluation

Log every turn: question, persona, loaded context, plan, queries issued, rows returned,
citations, final answer. That log is the eval set (§6).

Seed with ~30 questions drawn from P1 and P2, including questions the warehouse **cannot**
answer. Grade the plan separately from the prose:

| Dimension | Question |
|---|---|
| Metric selection | Did it pick the metric a domain expert would? |
| Grain | Right time grain, right geography, right population cut? |
| Refusal | Did it decline when the data does not support the question? |
| Attribution | Every number traceable; every citation real? |

Metric selection and refusal are the ones that matter. A wrong-but-confident proxy metric is
this product's characteristic failure, and prose quality will not reveal it.

---

## 8. Reporting issues

When something is wrong with the platform — a number that looks off, a stale dataset, a
broken page — either chat surface can file a GitHub issue. This is what closes P1's loop:
the researcher's unanswerable question becomes the backlog.

It is **one REST endpoint**, `POST /tools/report_issue` in `hub_api/issues.py`, not a second
MCP tool. The hub's own page calls it directly; the configured `ohdp-tools` connection calls
the same route because it is pointed at the narrow OpenAPI spec that module mounts. One
implementation, one set of guardrails, two callers.

Two things are worth knowing before changing it:

**The spec is deliberately narrow.** `/tools` is a *mounted sub-app*, so its `/openapi.json`
lists only what is deliberately put there — `report_issue` plus ADR-0025's three dashboard
operations, and nothing else. The configured tool connection turns every operation in the spec
it reads into a callable tool, so pointing it at hub-api's root spec would hand the chat model
`POST /api/chat`, a chat endpoint able to invoke itself. There is no per-operation allowlist on
the connection side, so that narrowing *is* the access control. `test_issues.py` asserts the
exact set, not a subset: adding a route to the mounted app hands the chat model a tool, and
that should be a decision rather than a side effect.

**There are two identities, and only one of them is a person.** A browser carries the hub's own
signed session cookie (`hub_api.auth`) and its issues are attributed to that email. The
configured tool connection arrives by cluster DNS carrying a shared bearer token, which proves
the caller is the chat surface and nothing about who is typing — so those issues are filed
anonymously rather than against an identity we would be inventing. The cost is that the rate
limit puts every chatbot report in one bucket: a global ceiling on how fast chat can fill the
tracker, at the price that one abuser locks out the rest for the hour.

This is the first **write** tool in the platform; everything else a chat user can reach is
read-only. Hence: every issue labelled `user-reported` plus its source, so the tracker can be
filtered or drained in one query; exact-title dedupe against open issues, because a model's
characteristic failure is filing the same report on every retry; credential-shaped strings
redacted before publication, because transcripts contain whatever the user pasted; and a
fine-grained PAT scoped to issues:write on one repo, so the worst case is recoverable noise.

Deviations from the M4 sketch this replaces, all deliberate: dedupe is exact normalized title
rather than embedding similarity (one API call, no model, no index — revisit when the tracker
is large enough that near-misses actually slip through); the rate limit is per hour rather
than per month, since it is a spam ceiling and not a quota; and issues are labelled by source
rather than by tier, because everyone signed in is tier `free` today.

**Not configured by default.** `OHDP_GITHUB_TOKEN` and `OHDP_GITHUB_ISSUES_REPO` unset means
this one endpoint answers 503 and nothing else changes — the same intended degradation as a
missing chat model key. Set `GITHUB_ISSUES_TOKEN` and `TOOLS_AUTH_TOKEN` as repo secrets to turn it on.

---

## 9. Build order

Each step should be demoable and independently reviewable.

| Step | Deliverable |
|---|---|
| **M3.0** | **Partly done.** Tool inventory verified against the live server (§2.1). The OM-Metric ↔ Cube-measure round-trip test (§3.5) is **not written** — still the most likely quiet failure. |
| **M3.1** | **Done.** `ohdp_agent`: Cube tools over REST, OM MCP client, Europe PMC tools. Unit-tested with no model in the loop. |
| **M3.2** | **Done, with two deviations** recorded at the top of `ohdp_agent/loop.py`: a manual loop rather than the SDK tool runner, and one phase rather than two (no user gate between plan and execute). SSE, quota gate and Postgres logging are in. |
| **M3.3** | **Partly done.** Glossary/domain seed content (`data/src/ohdp_orchestration/seed/`) syncs via the `openmetadata_seed_sync` Dagster asset. Personas and Context Center articles are still **not done** — until those land, `get_persona_context` 404s and the agent uses its built-in system prompt. |
| **M3.4** | **Partly done.** A chat UI exists — `apps/web`/`Chat.tsx`; hub-api serves the chat endpoint. Charts render through `render_dashboard` on hub-api's `/tools` app and are embedded in the transcript as sandboxed iframes (§5, ADR-0025). |
| **M3.5** | **Not done.** Every turn is logged to `chat_turns` in the shape §7 wants, so the eval set is accumulating; there is no harness and no seed question set. |
| **M4** | News retrieval. Issue reporting with dedup landed early — see §8, it is live on both surfaces, and dashboards-as-code landed with it (§5). |

**Deployed as:** `app.open-health-data-platform.org`, served by `charts/hub-api`'s own
Ingress. There is no oauth2-proxy wall in front any more — the hub does its own OIDC
sign-in (`hub_api.auth`), gated by `settings.allowed_emails_list` while this is a
single-operator surface. Everyone who signs in is tier `free` — ARCHITECTURE.md §5's real
IdP with a `tier` claim is still unbuilt.

M3.0 and M3.1 carry the real risk and involve no model at all. Do them first.

---

## 10. Open questions

1. ~~**Which Context Center tools are actually OSS in 2.0.1.**~~ **Settled (M3.0): all of
   them.** Nothing in §2.1 is Collate-gated, so §3.1 stands as written and no fallback to
   versioned system prompts is needed. The live gaps are configuration, not licensing —
   vector embeddings are off (killing `find_context` and `semantic_search`) and no persona
   is bound to the bot user. See §2.1.
2. **Cost per question.** Depends on whichever model and provider are configured; measure
   before setting the free-tier quota (§10.3 of ARCHITECTURE.md proposes 20/month).
   Prompt caching on the persona preamble and the `/v1/meta` surface is the first lever; both
   are stable across turns and across users.
3. **Correlation claims.** P2's journey ends at "does our data show anything," which invites
   exactly the spurious correlation §1 of this doc names as their failure mode. Decide whether
   the agent may compute cross-source correlations at all in M3, or only place two series side
   by side and let the human draw the line. Leaning toward side-by-side.
4. **Persona selection.** Explicit picker, or inferred from the question? Explicit is honest
   and testable; inferred is nicer. Start explicit.


---

## 11. Historical note: the Open WebUI surface

A second chat surface using Open WebUI was deployed during M3 but has since
been removed. The Hub UI (`apps/web`) and `hub-api` remain the only chat
surface. See the superseded [ADR-0017](decisions/0017-open-webui-chat-ui.md)
for the original rationale.

The `mcp-cube` MCP server that ADR-0017 built alongside Open WebUI was *not*
Open-WebUI-specific — it is a thin FastMCP wrapper over `ohdp_agent.cube`
(`apps/api/src/ohdp_mcp/server.py`) — and it is still deployed. The Hub UI
chatbot's own agent is now its caller, via the `mcp-cube` connection in
`apps/api/config/tools.yaml` (§2.1 covers the OpenMetadata MCP connection the
same tool-connection mechanism also carries).
