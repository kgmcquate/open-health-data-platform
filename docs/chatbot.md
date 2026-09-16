# Chatbot — design (M3)

**Status:** M3.0-M3.2 and a first M3.4 are built and deployable. §3.3's personas
and §3.5's CI-tested FQN round-trip are not; §5's Vega-Lite charts and §8's
ticket flow are not. See §9 for what each step's state actually is. A second chat
surface — Open WebUI over an MCP server for Cube — now runs alongside this one
([ADR-0017](decisions/0017-open-webui-chat-ui.md), model backend since
[ADR-0023](decisions/0023-openrouter-glm-model-backend.md)); see §11.

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
Claude tool-use loop in `hub-api`; every group below is a set of tools it can call.

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

These four tools have **two transports**. In this loop they are Anthropic tool
definitions called in-process. For any MCP client — Open WebUI today — the same
four are served over MCP streamable HTTP by `ohdp_mcp` (§11, ADR-0017), which
delegates to the same `CubeClient` and validates the same `CubeQuery`. The safety
property is in the model, not the transport, so it holds either way.

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

Seed content extends `catalog/openmetadata/seed/glossary.yml`.

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

**Model and API.** Claude Opus 5 (`claude-opus-5`) via the Anthropic Python SDK, adaptive
thinking, streamed to the UI over SSE. Run the loop with the SDK's tool runner rather than
hand-writing it — its per-turn hooks are where quota decrements, tool-call logging, and the
citation check hang.

**OM MCP client placement.** Run the MCP client *inside* hub-api (Python `mcp` SDK) rather than
using the Anthropic API's server-side MCP connector. The connector would have Anthropic's
servers call our catalog directly; it is less code, but it puts tool calls outside our logging
and quota path, which §6 requires us to record.

**OM authentication.** hub-api holds a single OpenMetadata bot PAT. Per-user catalog scoping is
deliberately not built: the data is public and identical for every user (§1.1), so there is
nothing to scope. Persona is selected by hub-api and passed explicitly.

---

## 5. Visualizations

Two paths, staged.

**M3 — inline, ephemeral.** The agent emits a **validated chart spec** (Vega-Lite) alongside
the result set; `hub-web` renders it in the chat thread. No write path, no deploy, and the
generated query is shown next to the chart. The spec is schema-validated and its data is bound
to the rows we just returned — the model never supplies data values, only encodings.

**M4 — durable, reviewable.** A "save as dashboard" action writes an
`apps/streamlit/pages/NN_<slug>.py` file and opens a PR. This is exactly the AI-authored
dashboard workflow ADR-0015 chose Streamlit for, and it inherits git review for free. By then
Streamlit should read Cube rather than Snowflake directly, so a saved dashboard and the chat
answer that spawned it cannot disagree about a number.

The chat UI stays in `hub-web`, not Streamlit — Streamlit sits behind a single-operator
oauth2-proxy wall (ADR-0015) and has no relationship to tiers, quota, or billing.

---

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
MCP tool. The hub's own page calls it directly; Open WebUI calls the same route because it is
pointed at the narrow OpenAPI spec that module mounts. One implementation, one set of
guardrails, two callers.

Two things are worth knowing before changing it:

**The spec is deliberately narrow.** `/tools` is a *mounted sub-app*, so its `/openapi.json`
lists exactly one operation. Open WebUI turns every operation in the spec it reads into a
callable tool — pointing it at hub-api's root spec would hand the chat model `POST /api/chat`,
a chat endpoint able to invoke itself. There is no per-operation allowlist on the Open WebUI
side, so that narrowing *is* the access control, and `test_issues.py` asserts it.

**There are two identities, and only one of them is a person.** A browser arrives through
oauth2-proxy carrying `X-Forwarded-Email` and its issues are attributed to that address. Open
WebUI arrives by cluster DNS carrying a shared bearer token, which proves the caller is Open
WebUI and nothing about who is typing — so those issues are filed anonymously rather than
against an identity we would be inventing. The cost is that the rate limit puts every chatbot
report in one bucket: a global ceiling on how fast chat can fill the tracker, at the price
that one abuser locks out the rest for the hour.

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
rather than by tier, because everyone past both walls is tier `free` today.

**Not configured by default.** `OHDP_GITHUB_TOKEN` and `OHDP_GITHUB_ISSUES_REPO` unset means
this one endpoint answers 503 and nothing else changes — the same intended degradation as the
Anthropic key. Set `GITHUB_ISSUES_TOKEN` and `TOOLS_AUTH_TOKEN` as repo secrets to turn it on.

---

## 9. Build order

Each step should be demoable and independently reviewable.

| Step | Deliverable |
|---|---|
| **M3.0** | **Partly done.** Tool inventory verified against the live server (§2.1). The OM-Metric ↔ Cube-measure round-trip test (§3.5) is **not written** — still the most likely quiet failure. |
| **M3.1** | **Done.** `ohdp_agent`: Cube tools over REST, OM MCP client, Europe PMC tools. Unit-tested with no model in the loop. |
| **M3.2** | **Done, with two deviations** recorded at the top of `ohdp_agent/loop.py`: a manual loop rather than the SDK tool runner, and one phase rather than two (no user gate between plan and execute). SSE, quota gate and Postgres logging are in. |
| **M3.3** | **Not done.** Personas and Context Center seed content in `catalog/openmetadata/seed/`. Until this lands, `get_persona_context` 404s and the agent uses its built-in system prompt. |
| **M3.4** | **Partly done.** A chat UI exists — one static page served by hub-api, not hub-web (see `hub_api/main.py` for why). Vega-Lite specs are **not** implemented; results render as a table plus the compiled SQL. |
| **M3.5** | **Not done.** Every turn is logged to `chat_turns` in the shape §7 wants, so the eval set is accumulating; there is no harness and no seed question set. |
| **M4** | Streamlit dashboard generation via PR; news retrieval. Issue reporting with dedup landed early — see §8, it is live on both surfaces. |

**Deployed as:** `app.open-health-data-platform.org`, behind an `oauth2-proxy` Google wall
(`platform/helm/values/oauth2-proxy-app.yaml`) that owns the hostname; `charts/hub-api` has
its own Ingress disabled so nothing reaches the agent without passing the wall. Everyone
past it is tier `free` — ARCHITECTURE.md §5's real IdP with a `tier` claim is still unbuilt.

M3.0 and M3.1 carry the real risk and involve no model at all. Do them first.

---

## 10. Open questions

1. ~~**Which Context Center tools are actually OSS in 2.0.1.**~~ **Settled (M3.0): all of
   them.** Nothing in §2.1 is Collate-gated, so §3.1 stands as written and no fallback to
   versioned system prompts is needed. The live gaps are configuration, not licensing —
   vector embeddings are off (killing `find_context` and `semantic_search`) and no persona
   is bound to the bot user. See §2.1.
2. **Cost per question.** Opus 5 at $5/$25 per MTok, with a plan phase that may load several
   `get_asset_context` documents. Measure before setting the free-tier quota (§10.3 of
   ARCHITECTURE.md proposes 20/month). Prompt caching on the persona preamble and the
   `/v1/meta` surface is the first lever; both are stable across turns and across users.
3. **Correlation claims.** P2's journey ends at "does our data show anything," which invites
   exactly the spurious correlation §1 of this doc names as their failure mode. Decide whether
   the agent may compute cross-source correlations at all in M3, or only place two series side
   by side and let the human draw the line. Leaning toward side-by-side.
4. **Persona selection.** Explicit picker, or inferred from the question? Explicit is honest
   and testable; inferred is nicer. Start explicit.

---

## 11. Open WebUI — the second chat surface (ADR-0017)

Everything above describes `hub-api`'s own UI at `app.open-health-data-platform.org`.
A second surface now runs at `chat.open-health-data-platform.org`: **Open WebUI**,
deployed from its official Helm chart, calling the semantic layer through
**`mcp-cube`** — our FastMCP server over the §2.2 tool layer.

```
chat.ohdp.org -> Traefik -> open-webui (Google SSO, its own)
                              |-> mcp-cube (ClusterIP) -> cube -> Snowflake
                              \-> openrouter.ai/api/v1  (GLM 5.3 Flash, ADR-0023)

app.ohdp.org  -> Traefik -> oauth2-proxy-app -> hub-api   (§1-§10, unchanged)
```

### 11.1 What Open WebUI does and does not get

It gets a real chat product for none of our code, and the full §2.2 Cube tool
surface with its safety properties intact — `CubeQuery` is validated in
`ohdp_mcp`, on our side of the wire.

It does **not** get: the quota gate (§6), the `chat_turns` log that is also the
eval set (§7), the Europe PMC tools and their citation check (§2.3), or
OpenMetadata context (§3). The two surfaces also no longer run the same model:
since [ADR-0023](decisions/0023-openrouter-glm-model-backend.md) this one is
GLM 5.3 Flash over OpenRouter, while hub-api's loop is Claude Opus 5 over the
Anthropic SDK with extended thinking (§4). **Those are the reasons hub-api is
still deployed**, and why
"which surface should exist in six months" is a question for evidence rather than
this document.

### 11.2 Post-install steps that are not in the chart

The MCP tool server registration and the restriction to a single model are now
seeded declaratively from `values/open-webui.yaml` (`TOOL_SERVER_CONNECTIONS`
and `OPENAI_API_CONFIGS`/`DEFAULT_MODELS`) rather than clicked through the
UI — but both are Open WebUI `PersistentConfig` values: the env var only
writes the initial row into Open WebUI's own database on first boot. After
that, whatever an admin sets in Settings is what persists across restarts,
chart upgrades included. If mcp-cube isn't showing four tools, or a model other
than `z-ai/glm-5.3-flash` is reachable, check Settings first before assuming the
chart value isn't applying — and note that the second of those matters more
under OpenRouter than it did under Anthropic, since one key there reaches
several hundred models at every price point (ADR-0023) — it may simply have already been overridden there. See
`charts/mcp-cube/templates/NOTES.txt` for the tool count.

One step remains manual, because Open WebUI has no env var for it at all:

- **Set the model's system prompt** (Settings → Admin → Models) to carry
  ARCHITECTURE.md §10.2's disclaimer — population-level, not clinical decision
  support. hub-api attaches this in code, where a model cannot forget it; here it
  is configuration, which is weaker. The MCP server also ships it in its
  `instructions`, so a client that honours those sees it regardless.

### 11.3 Access control

Open WebUI's own Google OAuth, not an oauth2-proxy wall — ADR-0017 explains why
this one surface departs from ADR-0007's pattern. Password sign-in is off; OAuth
sign-up is on; every new account lands in `DEFAULT_USER_ROLE=pending` and sees
nothing until an admin promotes it. **That pending role is the whole access
policy**, standing in for the email allowlist the other three walls use, and it is
what separates a public hostname from a metered OpenRouter key. There is no
quota gate behind it.

### 11.4 Token usage limits (ADR-0022)

The gap §11.3 calls out — no quota gate on this surface — is what
[`openwebui-token-tracking`](https://dartmouth.github.io/openwebui-token-tracking/)
closes: per-user/group daily credit allowances, enforced by routing the model
through a tracked "pipe" Function instead of the direct OpenAI-compatible
passthrough. The package is baked into the image
(`apps/open-webui/Dockerfile`, ADR-0022) and the database is migrated
automatically on every deploy (`platform/helm/manifests/open-webui-token-tracking-init-job.yaml`).
What's left is manual, because Open WebUI has no declarative path for custom
Function code or per-model visibility — the same limitation §11.2 already
lives with for MCP tool-server registration:

- **Register the Function** (Settings → Admin → Functions → New). Paste:

  ```python
  """
  title: OpenRouter Pipe
  author: (you)
  requirements: openwebui-token-tracking
  version: 0.1.0
  """
  from openwebui_token_tracking.pipes.openai import OpenAITrackedPipe

  Pipe = OpenAITrackedPipe
  ```

  `OpenAITrackedPipe`, not `AnthropicTrackedPipe` — since ADR-0023 the backend
  is OpenRouter, which is OpenAI-compatible, and this pipe exists for exactly
  that case (its docstring: "providers that are fully compliant with OpenAI's
  API specification... can also be used with this pipe by setting the respective
  values in the Valves").

  Name the Function `OpenRouter`. That name becomes the Function id
  (`openrouter`), which Open WebUI prefixes onto every model the pipe lists,
  and the pipe strips `{PROVIDER}.` back off before pricing the request — so
  three things must agree or the pipe shows an empty model list and nothing
  works: the Function name, the `PROVIDER` Valve, and `--provider` in the init
  Job's `pricing upsert`. All three are `openrouter`.

  Then set its Valves:

  | Valve | Value | Default from env? |
  |---|---|---|
  | `API_KEY` | the OpenRouter key | yes — `OPENAI_API_KEY`, already in the pod |
  | `API_BASE_URL` | `https://openrouter.ai/api/v1` | **no** — type it in |
  | `PROVIDER` | `openrouter` | **no** — type it in |

  `API_KEY` defaults from the pod's own `OPENAI_API_KEY` env var, which the
  chart renders from `openaiApiKeyExistingSecret` (`values/open-webui.yaml`) —
  confirm it picked that up rather than retyping it. The other two default to
  OpenAI's own endpoint and provider name, and the package reads no env var for
  either, so they are hand-entered. Leaving `API_BASE_URL` at its default sends
  chat traffic to `api.openai.com` with an OpenRouter key and fails 401.

- **Hide the untracked model.** Once the Function is enabled it registers a
  new selectable model (`openrouter.z-ai/glm-5.3-flash`, pulled from the
  pricing table the init Job seeds). The plain `z-ai/glm-5.3-flash` OpenAI-API
  model is still directly selectable and bypasses tracking entirely — set its
  visibility to Private/admin-only in Settings → Admin → Models so regular
  users can only reach the model through the tracked pipe.

- **Re-point the `health-data-analyst` custom model.** It's currently built
  on top of the untracked `z-ai/glm-5.3-flash` base model
  (`models/health-data-analyst-*.json`) — re-import it with its base model
  changed to the tracked pipe's model, or it's a second bypass.

- **Credit groups**, via `kubectl exec` into the running Open WebUI pod
  (`kubectl -n app exec -it deploy/open-webui -- sh`):

  ```sh
  owui-token-tracking credit-group create "power users" 2000 "extra daily allowance"
  owui-token-tracking user find --email someone@example.com
  owui-token-tracking credit-group add-user <user-id> "power users"
  ```

  Every user already gets the base allowance seeded by the init Job
  (`token_tracking_base_settings`, 1000 credits/day = $1 at the package's
  1000-credits-per-USD convention — which buys far more conversation against
  GLM 5.3 Flash at $0.075/$0.25 per MTok than it did against Sonnet at $3/$15,
  so revisit the number rather than assuming it still bites) — credit groups
  are additive on top of
  that, not a replacement for it.

This only covers Open WebUI's surface. hub-api's own quota gate (§6) is
separate and unaffected.
