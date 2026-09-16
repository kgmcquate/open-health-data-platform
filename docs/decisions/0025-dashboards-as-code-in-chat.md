# 0025 — Dashboards as code, rendered in the chat turn

**Status:** Accepted
**Amends:** [ADR-0016](0016-chat-agent-tool-surface.md)'s tool split — the fourth
Cube tool group lands on hub-api's OpenAPI `/tools` app rather than in
`ohdp_mcp`, for a transport reason given below. Implements the M3 half of
docs/chatbot.md §5 and replaces its "hub-web renders it" assumption.

## Context

Charts in Open WebUI were supposed to come from the code interpreter: the model
writes matplotlib, saves a figure, the image appears inline. It does not work,
and the reason is structural rather than a bug. `CODE_INTERPRETER_ENGINE` is
`pyodide` (values/open-webui.yaml — jupyter means running a kernel pod on a node
already at its memory ceiling), so `plt.savefig("chart.png")` writes into a
browser-side virtual filesystem that nothing ever reads. The model followed its
instructions and produced nothing the user could see.

Two further things were wrong with that path even where it works. A PNG is not
reviewable — you cannot diff it, and you cannot tell whether the numbers in it
came from the warehouse or from the model. And nothing survived the
conversation: every chart was rebuilt from scratch by the next person to ask.

Meanwhile docs/chatbot.md §5 already described what we wanted — a validated
Vega-Lite spec whose data is bound to rows we just returned, and a durable
"save as dashboard" path — but described it against `hub-web`, a surface that
does not exist on the Open WebUI side.

## Decision

A dashboard is a **spec**, not a picture and not plotting code.
`ohdp_agent.dashboard.DashboardSpec` is a name, a title, and up to six panels;
each panel is a `CubeQuery` — the same validated object every other execution
tool takes — plus a `Chart` saying which returned column goes on which axis.
`ohdp_agent/dashboards/*.yaml` holds the ones that were kept.

Three operations join hub-api's `/tools` app: `render_dashboard` (an ad hoc
spec), `list_saved_dashboards`, and `open_saved_dashboard`. The two that draw
answer with the rendered HTML under `Content-Type: text/html` and
`Content-Disposition: inline`, which is what makes Open WebUI show a tool result
as an interactive iframe — its Rich UI embed path, in core since 0.6.31 and
reachable by external OpenAPI tool servers, not only by its own Python tools.

**They live on the OpenAPI app rather than in `ohdp_mcp` because MCP cannot do
this.** An MCP tool result is JSON-RPC content with no HTTP response headers, so
there is no way for an MCP tool to ask for an embed. MCP Apps (SEP-1865) is the
standard that would change that, and Open WebUI's support for it is an open
issue, not a shipped feature. This is the same reason `report_issue` is already
an OpenAPI operation, so the split is one the deployment had made before.

The model writes neither HTML nor Vega-Lite. `Chart` is a closed six-type enum
and `build_vega_spec` compiles it. That is not tidiness: arbitrary Vega-Lite
carries `transform`, `datasets`, and `data.url`, and a `data.url` in a chat
model's output is a fetch issued from inside the reader's browser.

## Consequences

- **Numbers cannot be invented.** Rows reach the page from Cube through
  `CubeQuery`; a `Panel` has nowhere to put a value (`extra="forbid"`, asserted
  in `test_dashboard.py`). The model supplies encodings only — which is
  docs/chatbot.md §5's original property, now enforced by the type rather than
  by the prompt.
- **A saved dashboard cannot go stale.** It stores questions, not answers, so
  `open_saved_dashboard` shows today's numbers. It also cannot disagree with the
  semantic layer, which is ARCHITECTURE.md §1.5's whole point.
- **Every rendered card carries its own YAML source.** Keeping a dashboard is a
  copy-and-PR, and the model never has to retype the spec — so what gets
  committed is exactly what was rendered.
- **The model cannot read back what it drew.** Open WebUI's
  `(embed, context-for-the-model)` pair is only available to tools it runs
  in-process; an external server's HTTP body *is* the embed, and the model gets
  a fixed "result is active and visible to the user" string. So the prompt tells
  it to run `run_metric_query` first when it intends to say anything about the
  data. That is two queries per dashboard, which is cheap against ADR-0024's
  pre-aggregations and honest besides — a model should not narrate data it has
  not seen.
- **`IFRAME_CSP` must stay unset, or be widened deliberately.** The embed loads
  Vega from `cdn.jsdelivr.net`, and the CSP in Open WebUI's own hardening guide
  would block it. Tightening it means allowlisting that origin in `script-src`,
  not pasting the example.
- **The code interpreter stays enabled but is no longer the graphics path.**
  Arithmetic over a result set is still a reasonable use for it.
- Streamlit (ADR-0015) is untouched and still queries Snowflake directly. The
  obvious next step is for it to render the same `DashboardSpec` files through
  Cube, at which point a saved dashboard and the chat answer that produced it
  are provably the same numbers. That needs a Cube client and a Cube secret in
  the Streamlit chart, so it is a separate decision rather than a consequence of
  this one.
- Open WebUI pins matter more now: the embed contract is upstream behaviour, not
  an API we control. It is exercised by `test_dashboard.py` only on our side of
  the wire — the header pair and the HTML — so an upstream change to
  `process_tool_result` would surface as markup in a transcript rather than as a
  failing test.
