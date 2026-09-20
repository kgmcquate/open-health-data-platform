# 0022 — Per-user/group token usage limits on Open WebUI

**Status:** **Superseded / removed.** This mechanism was tied to the Open WebUI
surface, which has been deleted. hub-api's per-tier quota gate remains the only
chat spending control. This document is retained for historical context.

**Extends (historically):** [ADR-0017](0017-open-webui-chat-ui.md) — same
deployment, closed the gap its Consequences section named: "Cost control is
weaker on the new surface... the `pending` default role and the Anthropic
console's own spend limit are the controls until that changes."

## Context

Open WebUI (`chat.open-health-data-platform.org`) has no quota gate of its
own — unlike hub-api, which checks quota before invoking the model
(docs/chatbot.md §6). The only controls today are `DEFAULT_USER_ROLE=pending`
(an admin must promote every new account) and Anthropic's account-wide spend
limit, neither of which caps what one already-admitted user can spend.

Open WebUI has no built-in enforcement of per-user token caps — visibility
exists (usage can be observed), but capping it is an open feature request
(open-webui/open-webui#23323). Three options:

- **A native visibility-only Function** (e.g. a token/cost display filter).
  Doesn't solve the actual ask: nothing stops a user from spending without
  limit, it only shows what they spent after the fact.
- **A hand-rolled Filter Function** enforcing limits against a new table.
  Reinvents credit accounting, pricing-per-model, and group allowances that
  an existing library already does correctly.
- **[`openwebui-token-tracking`](https://dartmouth.github.io/openwebui-token-tracking/),**
  a pip package providing a Postgres-backed "tracked pipe" (a `pipe` Function
  that wraps the provider call, checks remaining credits before sending, and
  debits actual token cost after). Chosen.

## Decision

**The model is routed through a tracked pipe Function instead of Open
WebUI's native OpenAI-compatible passthrough**
(`openaiBaseApiUrl`/`enableOpenaiApi`, still in place for the admin-only raw
model — see below). The pipe checks a per-user daily credit allowance (base
allowance + any credit-group bonuses) before every request and blocks once
it's exhausted; this is the actual enforcement mechanism, not just an
observed metric.

**The package is baked into a custom Open WebUI image
(`apps/open-webui/Dockerfile`), not installed at runtime.** Every other
custom capability in this platform (hub-api, Streamlit, the pipeline, Cube)
is a Dockerfile + `build-images.yml` matrix entry + sha-pinned tag; Open
WebUI was the one exception, still on the pure upstream image
(`platform/helm/values/open-webui.yaml`). Installing the package at runtime
via a Function's `requirements:` frontmatter (Open WebUI's own supported
mechanism) would mean a PyPI fetch on every pod restart, on a node already at
its memory ceiling (ARCHITECTURE.md §4) — a cost a Dockerfile avoids for
free, and it keeps the deployment reproducible from git rather than adding a
second thing living only in the running container's filesystem.

**Database setup runs as a plain, idempotent Job
(`platform/helm/manifests/open-webui-token-tracking-init-job.yaml`), applied
by `kubectl` in `deploy-platform.yml`, not a Helm hook.** The precedent for a
bootstrap Job (`postgres-db-bootstrap`) is a hook inside the `platform-base`
chart; Open WebUI has no equivalent local chart to hook into, since it's
installed straight from the upstream `open-webui/open-webui` chart. A
standalone manifest, deleted and re-applied on every deploy (Jobs are
immutable), fits without inventing a wrapper chart around an upstream one.
It migrates Open WebUI's own Postgres — no new datastore — and upserts
pricing for `claude-sonnet-5`, since the package's bundled pricing table
predates that model.

**Registering the Function, hiding the untracked model, and managing credit
groups stay manual admin steps** (docs/chatbot.md §11.4). Open WebUI has no
declarative path for custom Function code or per-model visibility, the same
limitation ADR-0017 already accepted for MCP tool-server registration.

## Consequences

- **A fifth custom image** (`ohdp-open-webui`), one more `build-images.yml`
  matrix entry and ~a minute of CI per merge to `main`.
- **Registering the Function and hiding the raw model are still not
  reproducible from git** — the same class of gap ADR-0017's Consequences
  already named for MCP tool-server registration. A misconfigured or
  forgotten step here (in particular, forgetting to hide the untracked
  `claude-sonnet-5` model) silently defeats the whole point: a user can just
  pick the untracked model and bypass the cap. Worth a periodic manual check
  until Open WebUI grows a declarative option for model visibility.
- **The `health-data-analyst` custom model needs re-pointing** at the tracked
  pipe's model once it exists, or it's a second bypass of the same kind.
- **Anthropic's OpenAI-compatible endpoint is still used for the admin-only
  raw model** — nothing here removes it, it's just no longer reachable by
  non-admin users once hidden.
- **This only covers Open WebUI's surface.** hub-api's quota gate (§6) is
  separate and already existed; nothing here changes it.
- **Pricing must be kept current by hand.** `input_cost_credits`/
  `output_cost_credits` for `claude-sonnet-5` are set once by the init Job
  from a hardcoded estimate and not automatically refreshed if Anthropic's
  pricing changes — a drift risk the same way the base image tag and chart
  version already require manual bumps in step.

See [docs/chatbot.md](../chatbot.md) §11 for a historical summary.
