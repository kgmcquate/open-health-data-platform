# 0023 — OpenRouter + GLM 5.3 Flash as Open WebUI's model backend

**Status:** Accepted
**Supersedes in part:** [ADR-0017](0017-open-webui-chat-ui.md) — only its "model
backend is Anthropic through its OpenAI-compatible endpoint" decision. Everything
else in 0017 (the deployment, the MCP tool surface, Open WebUI's own Google SSO,
`DEFAULT_USER_ROLE=pending`) stands.
**Amends:** [ADR-0022](0022-token-usage-limits.md) — the tracked pipe changes
class, not purpose.

## Context

ADR-0017 pointed Open WebUI at `api.anthropic.com/v1` because Open WebUI speaks
OpenAI's API shape and Anthropic publishes a compatible surface. That worked, but
it bought the worst of both: a compatibility layer that exposes neither extended
thinking nor the full tool-use surface (0017 named this), one provider's models
only, and Sonnet's $3/$15 per-MTok rates on the surface that has no quota gate of
its own and whole access policy is "an admin promoted you" (§11.3).

The workload on this surface is not the one hub-api runs. It is tool-calling
against mcp-cube and the OpenMetadata MCP server, then prose over the rows that
come back — the reasoning is mostly in the tool results. That is a cheap-model
job, and it is metered by a per-user credit cap (ADR-0022) whose bite depends
entirely on the per-token price.

## Decision

**Open WebUI's model backend is OpenRouter (`https://openrouter.ai/api/v1`),
running `z-ai/glm-5.3-flash`.** OpenRouter is natively OpenAI-compatible, so
this removes a translation layer rather than adding one. GLM 5.3 Flash supports
tool calling (the hard requirement — without it mcp-cube is dead weight),
structured outputs and vision, at $0.075/$0.25 per MTok against Sonnet's $3/$15:
roughly 1/50th the cost per token on a surface open to every promoted Google
account.

**hub-api keeps Anthropic, directly.** Its loop (§4) uses the Anthropic SDK with
adaptive thinking, is quota-gated, and logs every turn as the eval set. Nothing
here touches it. The two surfaces now differ in model as well as in
instrumentation, which makes "which surface should exist in six months"
comparable on cost as well as on capability.

**The key is a separate repo secret, `OPENROUTER_API_KEY`,** landing in
`openwebui-secrets` under the entry name `OPENAI_API_KEY` (what the chart and
the tracked pipe both read). A distinct key means the public chat host's spend
can be capped and rotated at OpenRouter without touching hub-api's.

**`OPENAI_API_CONFIGS` pinning one `model_ids` entry becomes load-bearing
security, not tidiness.** Anthropic's `/v1/models` lists one lab's handful;
OpenRouter's lists several hundred models from every lab, all billable to the
same key. Without that pin the picker would offer models costing 50x the chosen
one to every promoted user.

**ADR-0022's tracked pipe becomes `OpenAITrackedPipe` instead of
`AnthropicTrackedPipe`** — same package, same credit accounting, the class the
package ships for OpenAI-compatible providers. Its `API_BASE_URL` and `PROVIDER`
Valves have no env-var default, so registration grows two typed-in fields
(docs/chatbot.md §11.4), and the init Job's pricing row moves to
`z-ai/glm-5.3-flash` / provider `openrouter`.

## Consequences

- **A different model answers on `chat.` than on `app.`** Answer quality on this
  surface is now a GLM question, not a Claude one. The system prompt
  (`models/health-data-analyst-*.json`) was written against Sonnet and is not
  retuned here; if tool-calling discipline or refusal behaviour regresses, that
  prompt is the first place to look, not the provider.
- **OpenRouter is a broker, so there is now a third party in the path.** Prompts
  traverse OpenRouter to Z.ai. The data is public health data and the catalog is
  public, so this is a change in surface area rather than in sensitivity — but
  it is a new hop, and OpenRouter's own routing may fall back between providers
  for the same slug.
- **Three strings must agree** — the Function name, its `PROVIDER` Valve, and
  the init Job's `--provider` — or the tracked pipe lists no models. Same class
  of manual, not-reproducible-from-git step ADR-0017 and ADR-0022 already
  accepted, with one more way to get it wrong.
- **Pricing drift is the same accepted risk as ADR-0022's,** now against a
  provider that reprices more often and adds a per-request fee this model does
  not capture. The credit costs are a rationing estimate, not billing.
- **The daily credit allowance no longer means what it used to.** 1000
  credits/day was sized against $3/$15 tokens; at $0.075/$0.25 it is ~40x more
  conversation. Re-size it rather than assume the cap still binds.
- **Reversible cheaply.** Going back is a base URL, a `model_ids` value, a
  pricing row and a secret — which is itself the argument for OpenRouter: the
  next model change is a one-line `model_ids` edit, not a provider migration.
