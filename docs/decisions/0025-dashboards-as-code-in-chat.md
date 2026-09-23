# 0025 — Dashboards as code, rendered in the chat turn

**Status:** Accepted. The HTML-embed path described below originally targeted
Open WebUI; after Open WebUI was removed, the Hub UI (`apps/web`) renders the
same HTML body as a sandboxed iframe. The spec-and-binding design is unchanged.

**Amends:** [ADR-0016](0016-chat-agent-tool-surface.md)'s tool split — the fourth
Cube tool group lands on hub-api's OpenAPI `/tools` app rather than in a
dedicated MCP server, because a raw MCP tool result is JSON-RPC content with no
HTTP response headers and therefore cannot ask for an embed. Implements the M3
half of docs/chatbot.md §5.

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
`Content-Disposition: inline`, which is the signal the chat UI uses to show a
tool result as an interactive iframe rather than as markup in the transcript.

**They live on the OpenAPI app rather than in a dedicated MCP server because MCP cannot do
this.** An MCP tool result is JSON-RPC content with no HTTP response headers, so
there is no way for an MCP tool to ask for an embed. MCP Apps (SEP-1865) is the
standard that would change that; at the time this was written, Open WebUI's
support for it was an open issue, not a shipped feature. This is the same reason `report_issue` is already
an OpenAPI operation, so the split is one the deployment had made before.

The model writes neither HTML nor Vega-Lite — it supplies a `Chart` (a closed
six-type enum) that `build_vega_spec` compiles. That is not tidiness: arbitrary
Vega-Lite carries `transform`, `datasets`, and `data.url`, and a `data.url` in a
chat model's output is a fetch issued from inside the reader's browser.

> **Amended.** The "closed enum" half of this decision was later reversed: panels
> now carry an authorable Vega-Lite `vega` spec, with only literal `data` values
> forbidden at any depth — a `data` block that is only a remote `url` reference
> (a choropleth's basemap geometry) is allowed; rows are still bound from the
> Cube query by the server. The
> saved-dashboard half — `list_saved_dashboards`, `open_saved_dashboard`, and the
> `ohdp_agent/dashboards/` package — was later removed, and has since returned in
> a different form: `save_dashboard`/`get_dashboard` on the same `/tools` app,
> backed by the `dashboards` table rather than by YAML files in the image, so
> keeping a dashboard no longer requires a deploy — or a PR, since
> [ADR-0028](0028-agent-published-dashboards-ranked-by-votes.md) made a save
> publish straight onto the public Dashboards page, ranked by reader votes.
> Neither one draws — the model passes a spec `get_dashboard` returned back into
> `render_dashboard` — so the embed-headers argument above still applies to
> exactly one operation. See `docs/chatbot.md §5` for the current shape.

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
- **The model cannot read back what it drew.** The configured tool connection's
  wrapper strips the HTTP response down to plain MCP content, so the model only
  gets the HTML body and not the headers. The prompt therefore tells it to run
  `run_metric_query` first when it intends to say anything about the data. That
  is two queries per dashboard, which is cheap against ADR-0024's
  pre-aggregations and honest besides — a model should not narrate data it has
  not seen.
- **`IFRAME_CSP` must stay unset, or be widened deliberately (historical Open
  WebUI detail).** The embed loads Vega from `cdn.jsdelivr.net`, and the CSP in
  Open WebUI's own hardening guide would block it. Tightening it means
  allowlisting that origin in `script-src`, not pasting the example.
- **The code interpreter stays enabled but is no longer the graphics path.**
  Arithmetic over a result set is still a reasonable use for it.
- Streamlit (ADR-0015) is untouched and still queries Snowflake directly. The
  obvious next step is for it to render the same `DashboardSpec` files through
  Cube, at which point a saved dashboard and the chat answer that produced it
  are provably the same numbers. That needs a Cube client and a Cube secret in
  the Streamlit chart, so it is a separate decision rather than a consequence of
  this one.
- **The embed HTML must contain no `&quot;`, and must escape `&`, `<` and `>`
  twice (historical Open WebUI detail).** Under Open WebUI the embed did not
  reach the browser as an HTTP body: it was placed on a `function_call_output`
  item, JSON-stringified into a token attribute, and then evaluated with
  `parseJSONString(decode(attr))` — an HTML-entity decode *before* the JSON
  parse. A `&quot;` in the document therefore decoded to a bare `"` inside a JSON
  string literal, `JSON.parse` failed, the fallback returned the raw string, and
  the embed was silently dropped. The same decode also undid one level of
  escaping, so a single escape would hand the frontend live markup built from
  model-supplied titles and warehouse values. `_esc` therefore leaves quotes raw
  (valid in text content, and JSON-safe) and escapes the three markup characters
  twice. `test_dashboard.py` replays the round trip so a regression fails there
  rather than in front of a user.
- Open WebUI-specific pin behaviour mattered because the embed contract was
  upstream behaviour, not an API we controlled. With Open WebUI removed, the Hub
  UI owns the iframe rendering; the header pair and the HTML are still exercised
  by `test_dashboard.py` on our side of the wire.
