"""Dashboards as code: an authorable Vega-Lite spec, rendered to an HTML card.

This is docs/chatbot.md §5's "validated chart spec" path. One property drives
every decision in here:

  - **The model writes the Vega-Lite, but never the data.** A `Panel` carries a
    `CubeQuery` and a `vega` spec — anything Vega-Lite accepts (`mark`,
    `encoding`, `transform`, `layer`, `params`, ...). The one thing it may not
    author is a `data` key, which is rejected everywhere, at any depth. Rows
    are fetched from Cube by the caller and bound by `bind_data` as
    `data.values`, so there is no `data.url` to fetch from inside the viewer's
    browser and no field through which a number can be invented. The query is
    the same validated `models.CubeQuery` every other execution tool takes
    (ARCHITECTURE.md §6).

  - **The spec is the artifact.** A `DashboardSpec` round-trips through YAML, and
    the rendered card carries that YAML. Rows are never part of it: a spec holds
    a *question*, not an answer, so it cannot disagree with the semantic layer.

Two things the server still does for the author, because Cube's result shape
makes them easy to get wrong:

  - **Field escaping.** Every Cube column name contains a dot, and Vega-Lite
    reads an unescaped dot as nested-object access, so an unescaped
    `"field": "cube.measure"` renders empty with no error. `bind_data` escapes
    member references and resolves Cube's granularity suffix (`cube.week`) —
    the single most likely way a panel fails silently.

  - **Theme and defaults.** The page owns one Vega `config` per theme and merges
    it at render time; `bind_data` supplies `width: container`/`autosize: fit`
    for a unit spec that did not set its own.

The rendered HTML is a full document for a sandboxed iframe (Open WebUI's Rich
UI embed, ADR-0025). It loads Vega from a CDN, reports its own height by
`postMessage`, and carries its own YAML source so the reader can see the exact
spec without the model retyping it.

Colour comes from the reference data-viz palette, unmodified: eight categorical
slots in fixed order, separately stepped for the dark surface rather than
flipped. Three light-mode slots sit under 3:1 against the light surface, which
obliges the relief rule — hence the per-panel data table, which this platform
wanted anyway for provenance (§6, "every answer shows its work").
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Annotated, Any

import yaml
from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from ohdp_agent.models import CubeQuery

# A column key as it appears in a Cube result row: `cube.field`, plus the
# optional granularity suffix Cube appends to a time dimension
# (`cube.week_ending.week`). Same shape discipline as `models.Member` — nothing
# here is concatenated into anything that parses as SQL or as a Vega-Lite path.
COLUMN_RE = re.compile(r"^[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)?$")
ColumnKey = Annotated[str, Field(pattern=COLUMN_RE.pattern, max_length=192)]

NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

MAX_PANELS = 6

# Rows past this are dropped before they reach the page. A Cube free-tier query
# tops out at 1000 rows (semantic/cube/cube.js), six panels of which is a ~1MB
# chat message for a chart no one can read. Truncation is reported in the panel
# footer rather than done silently.
RENDER_ROW_CAP = 500
# The table view is relief for the low-contrast palette slots, not a data
# export; it shows the head and says how much it left out.
TABLE_ROW_CAP = 50

class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _assert_no_data(node: Any) -> None:
    """Reject a `data` key anywhere in the spec, at any depth.

    The one thing the model may not author is the data: rows are bound from the
    panel's Cube query by `bind_data`. A `data` key in any position — top level,
    under `layer`, or a `lookup` transform's `from.data` — is a fetch from
    inside the viewer's browser, or a smuggled answer, and is refused here.
    """
    if isinstance(node, dict):
        if "data" in node:
            raise ValueError(
                "a panel's `vega` spec may not carry a `data` key — rows are "
                "bound from the panel's Cube query by the server"
            )
        for value in node.values():
            _assert_no_data(value)
    elif isinstance(node, list):
        for item in node:
            _assert_no_data(item)


def _no_data(spec: dict[str, Any]) -> dict[str, Any]:
    """`AfterValidator` for `VegaSpec`: allow everything but `data`."""
    _assert_no_data(spec)
    return spec


# An agent-authored Vega-Lite spec. Everything is allowed — `mark`, `encoding`,
# `transform`, `layer`, `params`, `resolve` — except a `data` key, at any depth.
# The rows are attached by `bind_data` from the panel's query.
VegaSpec = Annotated[dict[str, Any], AfterValidator(_no_data)]


class Panel(_Strict):
    title: str = Field(min_length=1, max_length=160)
    query: CubeQuery
    # The agent's Vega-Lite spec, minus its data (see `VegaSpec`).
    vega: VegaSpec
    # One sentence of what the panel shows. The chart should carry the
    # explanation; this is for the part a reader cannot see, such as a
    # population restriction that lives in the filters.
    caption: str | None = Field(default=None, max_length=400)


class DashboardSpec(_Strict):
    """One dashboard. The unit that gets committed to git."""

    name: Annotated[str, Field(pattern=NAME_RE.pattern, min_length=3, max_length=64)]
    title: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=800)
    panels: list[Panel] = Field(min_length=1, max_length=MAX_PANELS)

    @classmethod
    def from_yaml(cls, text: str) -> DashboardSpec:
        """Parse a committed spec file.

        `safe_load`, not `load`: these files are reviewed in a PR, but a loader
        that can construct arbitrary Python objects is not something to leave
        pointed at a directory a model is invited to propose additions to.
        """
        data = yaml.safe_load(text)
        if not isinstance(data, dict):
            raise ValueError("a dashboard file must be a YAML mapping")
        return cls.model_validate(data)

    def to_yaml(self) -> str:
        """The form a human commits. Round-trips through `from_yaml`."""
        # `exclude_defaults`, not `exclude_none`: a spec dumped with every empty
        # `filters: []` and `order: {}` is noise in a file a human has to read
        # and edit. Dropping a field that holds its default changes nothing on
        # the way back in, so this still round-trips through `from_yaml`.
        data = self.model_dump(mode="json", exclude_defaults=True)
        return str(
            yaml.safe_dump(data, sort_keys=False, default_flow_style=False, allow_unicode=True)
        )


# --- rendering ------------------------------------------------------------


class PanelData(_Strict):
    """A panel's rows, as the caller fetched them from Cube."""

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    rows: list[dict[str, Any]]
    row_count: int
    truncated: bool = False


def resolve_column(key: str, columns: list[str]) -> str:
    """Map a spec column onto a key Cube actually returned.

    Cube keys a time dimension by its granularity (`cube.week_ending.week`), and
    which suffix comes back depends on the query — so a spec written against
    `cube.week_ending` still has to find it. Exact match wins; otherwise the
    unique granularity-suffixed key does.

    Raising with the available columns is deliberate: that message goes back to
    the model as a tool error, and a wrong column name is the mistake it is best
    placed to fix on its own.
    """
    if key in columns:
        return key
    candidates = [c for c in columns if c.startswith(f"{key}.")]
    if len(candidates) == 1:
        return candidates[0]
    raise ValueError(
        f"column {key!r} is not in the query result. Available columns: {', '.join(columns)}"
    )


def _escape_field_reference(field: str, columns: list[str]) -> str:
    """Map an authored `field` reference onto the column Cube returned, escaped.

    Two corrections, both driven by how Cube keys its result rows:

      - **Granularity suffix.** A reference to `cube.week_end` must find the
        `cube.week_end.week` Cube actually returned.
      - **Dot escaping.** Vega-Lite reads an unescaped dot as nested-object
        access, so `"field": "cube.measure"` resolves to undefined and the chart
        renders empty with no error.

    Only member references (`cube.field`) are corrected. A `datum.`/`parent.`
    accessor, a field that is already escaped, or a transform output like
    `ratio` has its dots mean object access — or has none — and is returned
    untouched. A member that does not resolve raises here, so a typo surfaces
    as "could not be drawn" rather than as a silent empty chart.
    """
    if field.startswith("\\") or field.startswith(("datum.", "parent.")):
        return field
    if COLUMN_RE.match(field):
        return resolve_column(field, columns).replace(".", "\\.")
    return field


def _escape_fields(node: Any, columns: list[str]) -> None:
    """Escape every `field` reference in the spec, in place.

    Walks the whole tree — `encoding`, `transform`, `layer`, `facet` — so that
    whatever the model wrote, a field that names a returned column survives
    Vega's parse.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "field" and isinstance(value, str):
                node[key] = _escape_field_reference(value, columns)
            else:
                _escape_fields(value, columns)
    elif isinstance(node, list):
        for item in node:
            _escape_fields(item, columns)


# Composite views size their own children; a top-level `width: container` and
# `autosize: fit` are a unit-view convenience that would fight a `layer`/`facet`.
_COMPOSITE_KEYS = frozenset({"layer", "concat", "hconcat", "vconcat", "facet", "repeat", "spec"})


def _unit_defaults(spec: dict[str, Any]) -> dict[str, Any]:
    """Width/autosize defaults for a single-view spec; composites are left alone."""
    if any(key in spec for key in _COMPOSITE_KEYS):
        return {}
    defaults = {"width": "container", "autosize": {"type": "fit", "contains": "padding"}}
    return {k: v for k, v in defaults.items() if k not in spec}


_VL_SCHEMA = "https://vega.github.io/schema/vega-lite/v5.json"


def bind_data(spec: VegaSpec, rows: list[dict[str, Any]], columns: list[str]) -> dict[str, Any]:
    """Bind the Cube rows into an authorable Vega-Lite spec.

    The agent's `vega` spec carries no `data` (rejected at parse time by the
    `VegaSpec` validator); this is the only place rows are attached, and they
    are always attached as `data.values` — never a URL, never a dataset name —
    so the chart cannot fetch from inside the viewer's browser.

    No `config` here either: the page holds one config per theme and merges it
    at render time, so a single spec serves light and dark (see `_THEME_CONFIG`).

    Field references are escaped first (see `_escape_field_reference`), and a
    single-view spec gets `width: container`/`autosize: fit` when it did not set
    its own, so a bare `{mark, encoding}` matches the compiled-output rendering.
    """
    vl_spec = deepcopy(spec)
    _escape_fields(vl_spec, columns)
    return {
        **_unit_defaults(vl_spec),
        **vl_spec,
        "$schema": _VL_SCHEMA,
        "data": {"values": rows},
    }


# Two selected palettes, not one flipped: the dark column is the same eight hues
# re-stepped for the dark surface. Both orderings are validated — worst adjacent
# CVD ΔE 9.1 light / 8.4 dark against an ≥8 target — and the ordering itself is
# the safety mechanism, so slots are assigned in this order and never cycled.
_LIGHT = {
    "surface": "#fcfcfb",
    "text": "#0b0b0b",
    "secondary": "#52514e",
    "muted": "#898781",
    "grid": "#e1e0d9",
    "axis": "#c3c2b7",
    "border": "rgba(11,11,11,0.10)",
    "category": [
        "#2a78d6",
        "#eb6834",
        "#1baf7a",
        "#eda100",
        "#e87ba4",
        "#008300",
        "#4a3aa7",
        "#e34948",
    ],
}
_DARK = {
    "surface": "#1a1a19",
    "text": "#ffffff",
    "secondary": "#c3c2b7",
    "muted": "#898781",
    "grid": "#2c2c2a",
    "axis": "#383835",
    "border": "rgba(255,255,255,0.10)",
    "category": [
        "#3987e5",
        "#d95926",
        "#199e70",
        "#c98500",
        "#d55181",
        "#008300",
        "#9085e9",
        "#e66767",
    ],
}

_FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'


def _theme_config(theme: dict[str, Any]) -> dict[str, Any]:
    """The Vega-Lite `config` for one theme.

    Everything that differs between light and dark lives here, which is why
    `bind_data` can stay theme-free and the page can swap themes by
    re-embedding the same spec with the other config.
    """
    return {
        "background": None,
        "font": _FONT,
        "axis": {
            "labelColor": theme["muted"],
            "titleColor": theme["secondary"],
            "titleFontWeight": 500,
            "labelFontSize": 11,
            "titleFontSize": 11,
            "domainColor": theme["axis"],
            "tickColor": theme["axis"],
            "gridColor": theme["grid"],
            "gridWidth": 1,
        },
        "legend": {
            "labelColor": theme["secondary"],
            "titleColor": theme["secondary"],
            "labelFontSize": 11,
            "titleFontSize": 11,
            "symbolType": "square",
            "symbolSize": 80,
        },
        "view": {"stroke": None},
        "range": {"category": theme["category"]},
        # Single-series marks take slot 1 rather than Vega's own default blue.
        # `stroke` belongs under `bar` and nowhere else: as a global mark default
        # it also paints every *line* in the surface colour, which renders a line
        # chart as a row of disconnected points and looks like a data problem
        # rather than a styling one.
        "mark": {"color": theme["category"][0]},
        "bar": {"stroke": theme["surface"], "strokeWidth": 1},
        "scale": {"bandPaddingInner": 0.3},
    }


_THEME_CONFIG = {"light": _theme_config(_LIGHT), "dark": _theme_config(_DARK)}


# CDN pins, not floating majors. This page renders inside a chat client we do
# not control, on a machine we do not control; a silent major bump upstream
# would break every dashboard at once with no deploy on our side.
_VEGA_SCRIPTS = (
    "https://cdn.jsdelivr.net/npm/vega@5.30.0",
    "https://cdn.jsdelivr.net/npm/vega-lite@5.21.0",
    "https://cdn.jsdelivr.net/npm/vega-embed@6.26.0",
)


def _json_for_html(value: Any) -> str:
    """Serialize for embedding in a `<script>` element.

    Escaping `<`, `>` and `&` is what stops a string in the data closing the
    script element early. The iframe is sandboxed without same-origin, so the
    blast radius of a failure here is small — which is a reason to be careful
    with it, not a reason to skip it.
    """
    return (
        json.dumps(value, default=str)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def _esc(text: str | None) -> str:
    """Escape for HTML text content, in a form that survives the chat client.

    Two departures from `html.escape(..., quote=True)`, both forced by how Open
    WebUI hands an embed to its own frontend. `ToolCallDisplay.svelte` and
    `ConsecutiveDetailsGroup.svelte` HTML-entity-**decode** the JSON string
    carrying this document *before* parsing it.

    **Quotes are left raw.** A `&quot;` decodes to a bare `"` inside a JSON
    string literal, so `JSON.parse` fails; their `parseJSONString` returns the
    raw text instead of raising, the `Array.isArray(...)` guard rejects it, and
    the embed is dropped with no error anywhere — the dashboard simply never
    appears. A raw `"` is valid in HTML text content and crosses the wire as
    `\\"`, which the decode pass does not touch.

    **`&`, `<` and `>` are escaped twice.** That same pass would undo a single
    escape and hand the frontend live markup assembled from model-supplied
    titles and warehouse values. Escaping twice leaves exactly one level after
    the decode, which renders as the literal character. Should the client ever
    stop decoding, this degrades to a visible `&lt;` rather than to injected
    markup — the safe direction to fail in.

    Nothing escaped here may be interpolated into an HTML *attribute*; every
    call site is text content, which is what makes leaving quotes raw sound.
    `test_dashboard.py` replays the decode-then-parse round trip, so a future
    edit that reintroduces `&quot;` fails there rather than in front of a user.
    """
    return (text or "").replace("&", "&amp;amp;").replace("<", "&amp;lt;").replace(">", "&amp;gt;")


def _table_html(rows: list[dict[str, Any]], columns: list[str]) -> str:
    """The table view.

    Not optional: three light-mode palette slots sit below 3:1 against the light
    surface, and the relief rule for those is visible labels or a table. It
    doubles as the provenance §6 asks for — the reader sees the numbers behind
    the marks, not just the marks.
    """
    head = "".join(f"<th>{_esc(c)}</th>" for c in columns)
    body = []
    for row in rows[:TABLE_ROW_CAP]:
        cells = "".join(f"<td>{_esc(_format_cell(row.get(c)))}</td>" for c in columns)
        body.append(f"<tr>{cells}</tr>")
    more = ""
    if len(rows) > TABLE_ROW_CAP:
        more = f'<p class="note">Showing the first {TABLE_ROW_CAP} of {len(rows)} rows.</p>'
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>{more}"


def _format_cell(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:,.4g}"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def _columns_of(rows: list[dict[str, Any]]) -> list[str]:
    """Union of keys across rows, first-seen order.

    Cube omits a key from a row whose value is null, so reading only the first
    row loses columns — and the column it loses is the one the chart is keyed on
    often enough to matter.
    """
    seen: dict[str, None] = {}
    for row in rows:
        for key in row:
            seen.setdefault(key, None)
    return list(seen)


def render_html(spec: DashboardSpec, data: list[PanelData]) -> str:
    """The complete embeddable document for one dashboard.

    `data[i]` belongs to `spec.panels[i]`; the caller is responsible for having
    run the queries, which is what keeps this module free of I/O and therefore
    testable without a warehouse.
    """
    if len(data) != len(spec.panels):
        raise ValueError("every panel needs its own result set")

    panels_html: list[str] = []
    vega_specs: list[dict[str, Any] | None] = []

    for index, (panel, result) in enumerate(zip(spec.panels, data, strict=True)):
        columns = _columns_of(result.rows)
        rows = result.rows[:RENDER_ROW_CAP]

        try:
            vega_specs.append(bind_data(panel.vega, rows, columns))
            chart_html = f'<div class="chart" id="chart-{index}"></div>'
        except ValueError as exc:
            # One unplottable panel should not cost the reader the other five,
            # and the rows are still worth showing. The reason is printed rather
            # than swallowed, because it is usually a column-name typo.
            vega_specs.append(None)
            chart_html = f'<p class="error">This panel could not be drawn: {_esc(str(exc))}</p>'

        footnote = f"{result.row_count:,} rows"
        if result.truncated:
            footnote += " (truncated at the query limit)"
        if result.row_count > RENDER_ROW_CAP:
            footnote += f"; the chart shows the first {RENDER_ROW_CAP:,}"

        query_json = json.dumps(panel.query.to_cube_json(), indent=2)
        panels_html.append(
            f"""      <section class="panel">
        <h2>{_esc(panel.title)}</h2>
        {chart_html}
        {f'<p class="caption">{_esc(panel.caption)}</p>' if panel.caption else ""}
        <details>
          <summary>Data and query · {_esc(footnote)}</summary>
          {_table_html(result.rows, columns)}
          <pre class="query">{_esc(query_json)}</pre>
        </details>
      </section>"""
        )

    payload = {
        "specs": vega_specs,
        "config": _THEME_CONFIG,
    }
    scripts = "\n".join(f'  <script src="{src}"></script>' for src in _VEGA_SCRIPTS)
    description = (
        f'    <p class="description">{_esc(spec.description)}</p>' if spec.description else ""
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(spec.title)}</title>
{scripts}
<style>
{_CSS}
</style>
</head>
<body>
  <main class="dashboard">
    <header>
      <h1>{_esc(spec.title)}</h1>
{description}
    </header>
    <div class="panels">
{chr(10).join(panels_html)}
    </div>
    <footer>
      <p class="disclaimer">Population-level public health data from the Open Health
      Data Platform semantic layer. Not clinical decision support and not medical advice.</p>
      <details>
        <summary>Dashboard source · <code>{_esc(spec.name)}.yaml</code></summary>
        <p class="note">The spec this page renders, as YAML — the Cube queries and
        the Vega-Lite, never the rows.</p>
        <pre class="source">{_esc(spec.to_yaml())}</pre>
      </details>
    </footer>
  </main>
<script type="application/json" id="payload">{_json_for_html(payload)}</script>
<script>
{_JS}
</script>
</body>
</html>
"""


_CSS = """
:root {
  color-scheme: light;
  --surface: #fcfcfb;
  --plane: #f9f9f7;
  --text: #0b0b0b;
  --secondary: #52514e;
  --muted: #898781;
  --grid: #e1e0d9;
  --border: rgba(11, 11, 11, 0.10);
  --critical: #d03b3b;
}
@media (prefers-color-scheme: dark) {
  :root {
    color-scheme: dark;
    --surface: #1a1a19;
    --plane: #0d0d0d;
    --text: #ffffff;
    --secondary: #c3c2b7;
    --muted: #898781;
    --grid: #2c2c2a;
    --border: rgba(255, 255, 255, 0.10);
    --critical: #e66767;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0;
  padding: 16px;
  background: var(--plane);
  color: var(--text);
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  font-size: 14px;
  line-height: 1.5;
}
h1 { font-size: 1.15rem; margin: 0 0 4px; font-weight: 600; }
h2 { font-size: 0.95rem; margin: 0 0 10px; font-weight: 600; }
header { margin-bottom: 16px; }
.description { margin: 0; color: var(--secondary); max-width: 68ch; }
/* One column by default; two only when there is room, so a phone-width chat
   pane never gets a 200px-wide chart. */
/* `align-items: start` so a short panel keeps its own height instead of being
   stretched to match a taller neighbour — a horizontal bar chart sizes itself
   by category count, so neighbours in a row are routinely unequal, and the
   stretched version reads as a rendering fault inside the card. */
.panels { display: grid; gap: 14px; grid-template-columns: 1fr; align-items: start; }
@media (min-width: 760px) { .panels { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
.panel {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 14px;
  min-width: 0;
}
/* A lone panel has no neighbour to line up with, so it takes the full width. */
.panels > .panel:only-child { grid-column: 1 / -1; }
.chart { width: 100%; min-height: 260px; }
.caption { margin: 8px 0 0; color: var(--secondary); font-size: 0.85rem; }
.error { color: var(--critical); font-size: 0.85rem; margin: 8px 0 0; }
details { margin-top: 10px; }
summary {
  cursor: pointer;
  color: var(--muted);
  font-size: 0.8rem;
  list-style-position: outside;
}
summary:hover { color: var(--secondary); }
table {
  width: 100%;
  border-collapse: collapse;
  margin-top: 8px;
  font-size: 0.78rem;
  font-variant-numeric: tabular-nums;
  display: block;
  overflow-x: auto;
}
th, td {
  text-align: left;
  padding: 4px 10px 4px 0;
  border-bottom: 1px solid var(--grid);
  white-space: nowrap;
}
th { color: var(--secondary); font-weight: 500; }
td { color: var(--text); }
pre {
  margin: 8px 0 0;
  padding: 10px;
  background: var(--plane);
  border: 1px solid var(--border);
  border-radius: 6px;
  overflow-x: auto;
  font-size: 0.75rem;
  line-height: 1.45;
  white-space: pre;
}
.note { color: var(--muted); font-size: 0.75rem; margin: 6px 0 0; }
footer { margin-top: 16px; }
.disclaimer { color: var(--muted); font-size: 0.75rem; margin: 0 0 8px; max-width: 68ch; }
code { font-size: 0.95em; }
"""

_JS = """
(function () {
  var payload = JSON.parse(document.getElementById('payload').textContent);
  var media = window.matchMedia('(prefers-color-scheme: dark)');

  function reportHeight() {
    // Open WebUI's embed iframe is sandboxed without same-origin, so the parent
    // cannot measure this document. Without this it renders at a stub height.
    parent.postMessage(
      { type: 'iframe:height', height: document.documentElement.scrollHeight },
      '*'
    );
  }

  function draw() {
    var config = payload.config[media.matches ? 'dark' : 'light'];
    var pending = payload.specs.map(function (spec, i) {
      var el = document.getElementById('chart-' + i);
      if (!spec || !el) return Promise.resolve();
      return vegaEmbed(el, Object.assign({}, spec, { config: config }), {
        actions: false,
        renderer: 'svg'
      }).catch(function (err) {
        el.innerHTML = '';
        var p = document.createElement('p');
        p.className = 'error';
        p.textContent = 'This panel could not be drawn: ' + err;
        el.appendChild(p);
      });
    });
    Promise.all(pending).then(reportHeight);
  }

  window.addEventListener('load', function () {
    draw();
    reportHeight();
  });
  // A <details> opening changes the document height; so does a theme switch.
  new ResizeObserver(reportHeight).observe(document.body);
  media.addEventListener('change', draw);
})();
"""
