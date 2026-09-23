"""Dashboards as code: an authorable Vega-Lite spec, rendered to an HTML card.

This is docs/chatbot.md §5's "validated chart spec" path. One property drives
every decision in here:

  - **The model writes the Vega-Lite, but never the data.** A `DashboardSpec` carries a
    `CubeQuery` and a `vega` spec — anything Vega-Lite accepts (`mark`,
    `encoding`, `transform`, `layer`, `params`, ...). The one thing it may not
    author is literal data: a `data` block is allowed only as a remote `url`
    reference (a choropleth's basemap geometry), and literal `values` are
    rejected at any depth. Rows are fetched from Cube by the caller and bound
    by `bind_data` as
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
from jsonpath_ng.ext import parse as parse_jsonpath
from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from ohdp_agent.models import CubeQuery

# A column key as it appears in a Cube result row: `cube.field`, plus the
# optional granularity suffix Cube appends to a time dimension
# (`cube.week_ending.week`). Same shape discipline as `models.Member` — nothing
# here is concatenated into anything that parses as SQL or as a Vega-Lite path.
COLUMN_RE = re.compile(r"^[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)?$")
ColumnKey = Annotated[str, Field(pattern=COLUMN_RE.pattern, max_length=192)]

NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

# Where in a `vega` spec the Cube rows land, as a JSONPath:
# `$.transform[0].from.data.values` for a choropleth whose geography is a
# top-level `data.url`. The default is the top-level `$.data.values`.
DEFAULT_DATA_PATH = "$.data.values"

# Rows past this are dropped before they reach the page. A Cube free-tier query
# tops out at 1000 rows (semantic/cube/cube.js), which is a ~1MB chat message for
# a chart no one can read. Truncation is reported in the chart's data footer
# rather than done silently.
RENDER_ROW_CAP = 500
# The table view is relief for the low-contrast palette slots, not a data
# export; it shows the head and says how much it left out.
TABLE_ROW_CAP = 50


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# A remote geometry is the only `data` the model may author. Choropleths need
# their basemap as `{"url": "...", "format": {"type": "topojson", ...}}` — a
# geometry file, not a value — so a `data` block is fine when it is exactly
# that: a `url`, plus an optional `format`. Literal `values`, or a dataset
# `name`, stay forbidden: rows are bound from the panel's Cube query by
# `bind_data`, and a non-empty `values` list in the spec is a smuggled answer.
# An *empty* `{"values": []}`, though, carries no data at all — it's how a
# `lookup` transform's `from.data` (or the top-level `data`, for a non-map
# chart) stays valid, parseable Vega-Lite before `bind_data` overwrites that
# same `values` key at `data_path`, so it's allowed as a placeholder too.
_DATA_URL_KEYS = frozenset({"url", "format"})


def _assert_data_is_url_only(data: Any) -> None:
    if isinstance(data, dict):
        if set(data) <= _DATA_URL_KEYS and "url" in data:
            return
        if data == {"values": []}:
            return
    raise ValueError(
        "a panel's `vega` spec must not carry literal `data` values — a "
        "`data` block is allowed only as a remote reference of the form "
        '`{"url": "...", "format": ...}` (e.g. a choropleth basemap) or an '
        'empty placeholder `{"values": []}` for the rows `data_path` will '
        "bind; rows are bound from the panel's Cube query by the server"
    )


def _assert_no_data(node: Any) -> None:
    """Reject literal `data` values anywhere in the spec, at any depth.

    A `url` reference — the only thing a choropleth can use to reach its
    geometry — passes; anything else (`values`, `name`) in any position —
    top level, under `layer`, or a `lookup` transform's `from.data` — is a
    smuggled answer and is refused here.
    """
    if isinstance(node, dict):
        if "data" in node:
            _assert_data_is_url_only(node["data"])
        for value in node.values():
            _assert_no_data(value)
    elif isinstance(node, list):
        for item in node:
            _assert_no_data(item)


def _no_data(spec: dict[str, Any]) -> dict[str, Any]:
    """`AfterValidator` for `VegaSpec`: allow everything but literal `data`."""
    _assert_no_data(spec)
    return spec


# Vega-Lite picks a transform by which of these keys it carries — there is no
# `type` discriminator. Low-level Vega's `{"type": "formula", "expr": ...}`
# therefore matches nothing, and vega-lite throws while compiling: the card
# renders empty, in the browser, where this server never sees it. Matched
# case-insensitively so a `timeUnit`/`timeunit` spelling can't fail a spec
# that would in fact have drawn.
_TRANSFORM_KEYS = frozenset(
    {
        "aggregate",
        "bin",
        "calculate",
        "density",
        "extent",
        "filter",
        "flatten",
        "fold",
        "impute",
        "joinaggregate",
        "loess",
        "lookup",
        "pivot",
        "quantile",
        "regression",
        "sample",
        "stack",
        "timeunit",
        "window",
    }
)


def _assert_vega_lite_transforms(node: Any) -> None:
    """Reject a Vega transform written into a Vega-Lite spec, at any depth.

    The failure this prevents is entirely client-side — vega-lite rejects the
    unknown step, nothing draws, and the tool still answered 200 — so it has
    to be caught before the embed is built or it is caught by nobody.
    """
    if isinstance(node, dict):
        steps = node.get("transform")
        if isinstance(steps, list):
            for step in steps:
                if isinstance(step, dict) and not {k.lower() for k in step} & _TRANSFORM_KEYS:
                    named = ", ".join(sorted(step)) or "no keys at all"
                    raise ValueError(
                        f"a `transform` step carrying {named} names no Vega-Lite "
                        "transform. Vega-Lite has no `type` discriminator — a step is "
                        "identified by its own key, e.g. `calculate`, `filter`, "
                        "`lookup`, `window`. In particular a Vega-style "
                        '`{"type": "formula", "expr": ...}` is written '
                        '`{"calculate": ..., "as": ...}` in Vega-Lite'
                    )
        for value in node.values():
            _assert_vega_lite_transforms(value)
    elif isinstance(node, list):
        for item in node:
            _assert_vega_lite_transforms(item)


# An agent-authored Vega-Lite spec. Everything is allowed — `mark`, `encoding`,
# `transform`, `layer`, `params`, `resolve` — and a `data` block only as a
# remote `url` reference (see above). Rows are still attached by `bind_data`
# from the panel's query.
VegaSpec = Annotated[dict[str, Any], AfterValidator(_no_data)]


class DashboardSpec(_Strict):
    """One chart. The unit that gets committed to git."""

    name: Annotated[str, Field(pattern=NAME_RE.pattern, min_length=3, max_length=64)]
    title: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=800)
    query: CubeQuery
    # The agent's Vega-Lite spec, minus its data (see `VegaSpec`).
    vega: VegaSpec
    # Where `bind_data` attaches the query's rows, as a JSONPath into `vega` —
    # `$.transform[0].from.data.values` for a choropleth whose geography is a
    # top-level `data.url`. The default is the top-level `$.data.values`.
    data_path: Annotated[str, AfterValidator(_valid_data_path)] = DEFAULT_DATA_PATH
    # One sentence of what the chart shows. The chart should carry the
    # explanation; this is for the part a reader cannot see, such as a
    # population restriction that lives in the filters.
    caption: str | None = Field(default=None, max_length=400)

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


class ChartData(_Strict):
    """The chart's rows, as the caller fetched them from Cube."""

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    rows: list[dict[str, Any]]
    row_count: int
    truncated: bool = False


def resolve_column(key: str, columns: list[str]) -> str:
    """Map a spec column onto a key Cube actually returned.

    Two ways a spec's name and Cube's column can legitimately differ:

      - **Granularity suffix.** Cube keys a time dimension by its granularity
        (`cube.week_ending.week`), and which suffix comes back depends on the
        query — so a spec written against `cube.week_ending` still has to
        find it. `key` is a *prefix* of the returned column here.
      - **Bare member name.** A `lookup`'s `from.key`/`from.fields` sometimes
        names a dimension without its cube (`fips` for `immunization.fips`) —
        there's no query context there to spell the prefix out. `key` is a
        *suffix* of the returned column here.

    Exact match wins; otherwise a uniquely-matching prefix or suffix does.
    Raising with the available columns is deliberate: that message goes back
    to the model as a tool error, and a wrong column name is the mistake it is
    best placed to fix on its own.
    """
    if key in columns:
        return key
    prefix_candidates = [c for c in columns if c.startswith(f"{key}.")]
    if len(prefix_candidates) == 1:
        return prefix_candidates[0]
    suffix_candidates = [c for c in columns if c.endswith(f".{key}")]
    if len(suffix_candidates) == 1:
        return suffix_candidates[0]
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
    accessor, a `properties.` accessor (a GeoJSON/TopoJSON feature's own
    attributes — the base `data` of every choropleth, never a Cube cube name),
    a field that is already escaped, or a transform output like `ratio` has
    its dots mean object access — or has none — and is returned untouched. A
    member that does not resolve raises here, so a typo surfaces as "could not
    be drawn" rather than as a silent empty chart.
    """
    if field.startswith("\\") or field.startswith(("datum.", "parent.", "properties.")):
        return field
    if COLUMN_RE.match(field):
        return resolve_column(field, columns).replace(".", "\\.")
    return field


def _resolve_lookup_field(field: str, columns: list[str]) -> str:
    """Resolve+escape a `lookup` transform's `from.key`/`from.fields` entry.

    These always name a column in the just-injected Cube rows — a choropleth's
    `from.data` holds nothing else — so, unlike `_escape_field_reference`,
    resolution isn't gated behind `COLUMN_RE`: a bare name like `fips` is
    resolved the same as a qualified `cube.fips` would be (`resolve_column`'s
    suffix match covers exactly this), and only a name matching no column at
    all — the actual typo case — raises.
    """
    if field.startswith("\\"):
        return field
    return resolve_column(field, columns).replace(".", "\\.")


def _escape_fields(node: Any, columns: list[str]) -> None:
    """Escape every `field` reference in the spec, in place.

    Walks the whole tree — `encoding`, `transform`, `layer`, `facet` — so that
    whatever the model wrote, a field that names a returned column survives
    Vega's parse. A `lookup` transform's `from.key`/`from.fields` get the same
    treatment: they aren't `field` entries, but they name Cube columns just as
    surely, and the same silent-empty-chart failure mode applies (see
    `_resolve_lookup_field`). When the model didn't give `as`, the resolved-to
    column's full name would otherwise become the joined property's name — but
    `encoding`/`tooltip` elsewhere in the spec is written against whatever name
    the model actually typed in `fields`, so that becomes `as` instead, and the
    join keeps writing under the name the rest of the spec already expects.
    That default `as` is run through `_escape_field_reference` — the same
    COLUMN_RE-gated function applied to every `encoding`/`tooltip` field —
    rather than kept as the model's raw text: a bare short name (no dot) is
    unaffected either way, but a model that typed the full `cube.member` in
    `fields` (as the prompt tells it to everywhere else) gets that same string
    dot-escaped in `encoding` too, so `as` and the field that reads it agree on
    one literal property name instead of Vega-Lite reading one as
    nested-object access.

    `as` is set on the *transform*, beside `lookup` and `from` — Vega-Lite's
    own schema, confirmed against its docs, has no `from.as`; a value written
    there is a silently ignored extra property, not a rename. Setting it in
    the wrong place would still pass every check in this module (the
    structure `bind_data` returns would look correct) while doing nothing at
    all in the browser, which is worse than not setting it — the failure
    would be invisible on this side of the wire too. Because this now mutates
    the transform dict (`node`) while walking it, the loop below iterates a
    snapshot of `node.items()`, not the live dict.

    `from.fields` is required, not optional, on every `lookup` whose `from.data`
    is the Cube-rows placeholder: Vega-Lite treats an omitted `fields` as
    "attach the whole matched row as one nested object" — silently, since that
    is valid Vega-Lite — and this module has no way to know, or rewrite,
    whatever nested path the rest of the spec would need to reach a single
    column back out of that object. A fieldless lookup against Cube rows is
    rejected here instead, before it can become a spec that renders with every
    value NaN and no error anywhere.

    A `lookup` can run the other way, too: the model's own rows as the primary
    `data`, joined *against* a remote basemap (`from.data.url`) to pull
    geometry onto each row. There, `from.key`/`from.fields` name a column in
    *that* remote source — a GeoJSON/TopoJSON feature's `id`/`properties.*`,
    never a Cube column — so resolving or requiring them against `columns`
    would be wrong in exactly the way `properties.*` was wrong in `encoding`.
    `VegaSpec`'s validator already guarantees `from.data` is either that exact
    `{"url": ..., "format": ...}` shape or the `{"values": []}` placeholder, so
    the two are told apart the same way here.
    """
    if isinstance(node, dict):
        for key, value in list(node.items()):
            if key == "field" and isinstance(value, str):
                node[key] = _escape_field_reference(value, columns)
            elif key == "from" and isinstance(value, dict):
                from_data = value.get("data")
                against_remote_geometry = isinstance(from_data, dict) and "url" in from_data
                if not against_remote_geometry:
                    if isinstance(value.get("key"), str):
                        value["key"] = _resolve_lookup_field(value["key"], columns)
                    fields = value.get("fields")
                    if not isinstance(fields, list) or not all(isinstance(f, str) for f in fields):
                        raise ValueError(
                            "a `lookup` transform must list `from.fields` — the columns to "
                            "join in by name. Without it Vega-Lite attaches the whole matched "
                            "row as one nested object, which nothing else in the spec can "
                            f"then reference by its own column name. Available columns: "
                            f"{', '.join(columns)}"
                        )
                    # No `as`: keep the joined properties named against what
                    # `encoding`/`tooltip` elsewhere in the spec expects — the
                    # same escaping an `encoding.field` with this text would get,
                    # so the two agree on the literal property name. It belongs
                    # on `node` (the transform), not `value` (`from`) — see above.
                    if "as" not in node:
                        node["as"] = [_escape_field_reference(f, columns) for f in fields]
                    value["fields"] = [_resolve_lookup_field(f, columns) for f in fields]
                _escape_fields(value, columns)
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


def _valid_data_path(value: str) -> str:
    """`AfterValidator` for `data_path`: it must parse as a JSONPath."""
    try:
        parse_jsonpath(value)
    except Exception as exc:
        raise ValueError(f"`data_path` must be a valid JSONPath: {exc}") from exc
    return value


# A JSONPath is split into parent path plus final selector so `values` can be
# created when absent. Everything else — matching, indexing, syntax — is the
# library's job; this regex only peels off one trailing `.key`, `['key']`, or
# `[0]`.
_TAIL_RE = re.compile(
    r"^(?P<parent>\$.*?)(?:\['(?P<key>[^'\\]*)'\]|\[(?P<idx>\d+)\]"
    r"|\.(?P<dot>[A-Za-z_][A-Za-z0-9_]*))$"
)


def _split_data_path(data_path: str) -> re.Match[str]:
    """Peel a `data_path` into its holder path plus final selector.

    One place parses the tail, because two callers need the split: the
    injection below, and the placeholder check that runs before it.
    """
    match = _TAIL_RE.match(data_path)
    if match is None:
        raise ValueError(f"`data_path` {data_path!r} must end in a key or index")
    return match


def _row_placeholders(node: Any, path: str = "$") -> list[tuple[str, dict[str, Any]]]:
    """Every empty `{"values": []}` row placeholder in the spec, with its path.

    The path is returned as a `data_path` a model can paste back — `$`, then
    each key as `.key` and each list position as `[i]`, which is the spelling
    `_TAIL_RE` and `jsonpath_ng` both read. `_assert_data_path_binds_rows`
    uses these to say where the rows *should* have gone, and identifies the
    blocks themselves (by object identity) to say which ones stayed empty.
    """
    found: list[tuple[str, dict[str, Any]]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{path}.{key}"
            if key == "data" and value == {"values": []}:
                found.append((f"{child}.values", value))
            else:
                found.extend(_row_placeholders(value, child))
    elif isinstance(node, list):
        for index, item in enumerate(node):
            found.extend(_row_placeholders(item, f"{path}[{index}]"))
    return found


def _assert_data_path_binds_rows(spec: dict[str, Any], data_path: str) -> None:
    """Reject a `data_path` that puts the rows anywhere but at the rows' spot.

    `VegaSpec` already guarantees that every `data` block in the spec is one of
    two things — a remote `{"url": ..., "format": ...}` geometry reference, or
    the empty `{"values": []}` placeholder — so this only has to check that
    `data_path` picked the second kind. Both ways of getting it wrong are
    invisible on this side of the wire and fatal on the other, which is why
    they are caught here rather than left to the browser (the same reasoning as
    `_assert_vega_lite_transforms`):

      - **Rows onto the basemap.** A choropleth that leaves `data_path` at its
        default `$.data.values` writes the Cube rows in beside the geometry's
        `url`. Vega-Lite reads a block carrying `values` as inline data, so it
        applies `format: {"type": "topojson", "feature": ...}` to an array of
        Cube rows and `topojsonFeature` throws on the missing `objects` —
        `TypeError: Cannot read properties of undefined (reading 'states')`,
        in the reader's browser, from a tool call that answered 200.

      - **A placeholder left empty.** The rows then bind somewhere else and the
        `lookup` joins against `[]`, which is valid Vega-Lite: the map draws,
        every joined value is missing, and nothing anywhere reports it.

    The suggested path in each message is the placeholder's own, so a failed
    render comes back as a one-line correction rather than a puzzle.
    """
    parent_path = _split_data_path(data_path).group("parent") or "$"
    targets = [match.value for match in parse_jsonpath(parent_path).find(spec)]
    placeholders = _row_placeholders(spec)

    geometry = next((t for t in targets if isinstance(t, dict) and "url" in t), None)
    if geometry is not None:
        suggestion = placeholders[0][0] if placeholders else "$.transform[0].from.data.values"
        raise ValueError(
            f"`data_path` {data_path!r} would bind the query's rows onto the "
            f"basemap geometry at {geometry['url']!r}. Rows written beside a "
            "`url` make Vega-Lite read that block as inline data and apply its "
            "`topojson` format to the Cube rows, which throws in the browser "
            "and draws nothing. Leave the geometry `data` alone and point "
            "`data_path` at the row placeholder the `lookup` transform joins "
            f"from, i.e. {suggestion!r}"
        )

    unbound = [path for path, block in placeholders if not any(block is t for t in targets)]
    if unbound:
        listed = ", ".join(repr(path) for path in unbound)
        raise ValueError(
            f"`data_path` {data_path!r} does not bind the empty "
            '`{"values": []}` row placeholder this spec carries at '
            f"{listed}. That placeholder would stay empty, so a `lookup` "
            "reading it joins against no rows — the chart draws with every "
            f"joined value missing and no error. Set `data_path` to "
            f"{unbound[0]!r}"
        )


def _inject_values(spec: Any, data_path: str, rows: list[dict[str, Any]]) -> None:
    """Attach `rows` at `data_path` inside `spec`, in place.

    The path must name parts of the spec the model actually wrote — a wrong
    turn is a typo in the path, not something to silently create, so a missing
    parent raises. The one exception is a `data` key, which the server owns
    (a missing `data` at the end of the parent path is created); the final
    key, always `values` by convention, may be absent.
    """
    match = _split_data_path(data_path)
    parent_path = match.group("parent") or "$"
    last_key = match.group("key") or match.group("dot")
    last_idx = match.group("idx")

    parents = parse_jsonpath(parent_path).find(spec)
    if not parents and (parent_path.endswith("['data']") or parent_path.endswith(".data")):
        # The server owns `data` keys; create the one this path expects.
        holder_match = _TAIL_RE.match(parent_path)
        assert holder_match is not None
        holder_path = holder_match.group("parent") or "$"
        for holder in parse_jsonpath(holder_path).find(spec):
            if isinstance(holder.value, dict):
                holder.value["data"] = {}
        parents = parse_jsonpath(parent_path).find(spec)
    if not parents:
        raise ValueError(f"`data_path` {data_path!r} does not name an existing part of the spec")
    for parent in parents:
        if isinstance(parent.value, list):
            parent.value[int(last_idx or last_key or 0)] = rows
        else:
            parent.value[last_key] = rows


def bind_data(
    spec: VegaSpec,
    rows: list[dict[str, Any]],
    columns: list[str],
    data_path: str = DEFAULT_DATA_PATH,
) -> dict[str, Any]:
    """Bind the Cube rows into an authorable Vega-Lite spec.

    The agent's `vega` spec may carry a remote `data.url` reference (rejected
    at parse time unless it is exactly that), but never `values`; this is the
    only place rows are attached, always as `data.values` — never a dataset
    name — so the numbers still come only from the Cube query. The default
    `$.data.values` lands them at the top level; a choropleth points
    `data_path` at its lookup source instead
    (`$.transform[0].from.data.values`), leaving the top-level `data.url`
    geometry reference in place.

    No `config` here either: the page holds one config per theme and merges it
    at render time, so a single spec serves light and dark (see `_THEME_CONFIG`).

    Field references are escaped first (see `_escape_field_reference`), and a
    single-view spec gets `width: container`/`autosize: fit` when it did not set
    its own, so a bare `{mark, encoding}` matches the compiled-output rendering.

    A `data_path` that names the wrong block raises before anything is bound
    (`_assert_data_path_binds_rows`): rows over a basemap `url`, or a
    placeholder left empty, are failures only the browser would otherwise see.
    """
    vl_spec = deepcopy(spec)
    _assert_vega_lite_transforms(vl_spec)
    # Before `_escape_fields`, not after: a `data_path` pointing at the wrong
    # block is the more fundamental mistake, and a spec that has both that and a
    # column typo should be told about the path first — fixing the path is what
    # moves the columns into the source the `lookup` actually reads.
    _assert_data_path_binds_rows(vl_spec, data_path)
    _escape_fields(vl_spec, columns)
    bound = {**_unit_defaults(vl_spec), **vl_spec, "$schema": _VL_SCHEMA}
    _inject_values(bound, data_path, rows)
    return bound


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


def theme_config() -> dict[str, Any]:
    """The Vega `config` per theme, for a caller that binds a spec itself.

    The embed page below merges these at render time; the hub's own Dashboards
    page (`hub_api.content`'s data route) renders a bound spec in the browser
    instead and needs the same two configs, or its charts come out in
    Vega-Lite's default palette rather than this platform's.
    """
    return deepcopy(_THEME_CONFIG)


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


def columns_of(rows: list[dict[str, Any]]) -> list[str]:
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


def render_html(spec: DashboardSpec, data: ChartData) -> str:
    """The complete embeddable document for one chart.

    `data` is the result of running `spec.query`; the caller is responsible for
    having run the query, which is what keeps this module free of I/O and
    therefore testable without a warehouse.

    Raises `ValueError` (from `bind_data`) if the spec references a column the
    query did not return. That used to be swallowed into an in-page error
    paragraph so a bad spec would not cost the reader the rows — but the embed
    is the entire HTTP response Open WebUI's Rich UI path renders, and the
    model never gets any of that body back (ADR-0025's "The model cannot read
    back what it drew"), so a caught-and-printed error was invisible to the
    one party that could fix the spec. The caller now decides what to do with
    the failure instead.
    """
    columns = columns_of(data.rows)
    rows = data.rows[:RENDER_ROW_CAP]

    vega_spec = bind_data(spec.vega, rows, columns, spec.data_path)
    chart_html = '<div class="chart" id="chart"></div>'

    footnote = f"{data.row_count:,} rows"
    if data.truncated:
        footnote += " (truncated at the query limit)"
    if data.row_count > RENDER_ROW_CAP:
        footnote += f"; the chart shows the first {RENDER_ROW_CAP:,}"

    query_json = json.dumps(spec.query.to_cube_json(), indent=2)
    panel_html = f"""    <section class="panel">
      {chart_html}
      {f'<p class="caption">{_esc(spec.caption)}</p>' if spec.caption else ""}
      <details>
        <summary>Data and query · {_esc(footnote)}</summary>
        {_table_html(data.rows, columns)}
        <pre class="query">{_esc(query_json)}</pre>
      </details>
    </section>"""

    payload = {
        "spec": vega_spec,
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
{panel_html}
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
.panel {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 14px;
  min-width: 0;
}
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
/* Scoped to the panel, never bare `table`/`th`/`td`: vega-tooltip builds the
   hover tooltip as its own <table> and appends it to <body>, outside any
   panel. A bare `td { color: ... }` there is a *direct* match on the
   tooltip's value cells, and a direct match beats the colour those cells only
   inherit from `#vg-tooltip-element` — so the dark theme's near-white --text
   landed on the tooltip's own pale background and the values became
   unreadable. Keep these selectors anchored to `.panel`. */
.panel table {
  width: 100%;
  border-collapse: collapse;
  margin-top: 8px;
  font-size: 0.78rem;
  font-variant-numeric: tabular-nums;
  display: block;
  overflow-x: auto;
}
.panel th, .panel td {
  text-align: left;
  padding: 4px 10px 4px 0;
  border-bottom: 1px solid var(--grid);
  white-space: nowrap;
}
.panel th { color: var(--secondary); font-weight: 500; }
.panel td { color: var(--text); }
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
    // `document.body.scrollHeight`, not `document.documentElement.scrollHeight`:
    // the latter is clamped to at least the iframe's viewport height, so once the
    // parent has framed the embed taller than its content, the reported height can
    // never shrink back down — which shows up as empty space below a collapsed
    // <details>. The body's scroll height is the real content height.
    parent.postMessage(
      { type: 'iframe:height', height: document.body.scrollHeight },
      '*'
    );
  }

  function draw() {
    var config = payload.config[media.matches ? 'dark' : 'light'];
    var spec = payload.spec;
    var el = document.getElementById('chart');
    if (!spec || !el) return Promise.resolve();
    return vegaEmbed(el, Object.assign({}, spec, { config: config }), {
      actions: false,
      renderer: 'svg',
      // vega-tooltip styles itself, from its own stylesheet, on an element it
      // appends to <body> — the page's CSS does not reach it and should not
      // try to. Its own `theme` is the supported way to keep the tooltip on
      // the same surface as the chart; `draw` re-runs on a theme change.
      tooltip: { theme: media.matches ? 'dark' : 'light' }
    }).catch(function (err) {
      el.innerHTML = '';
      var p = document.createElement('p');
      p.className = 'error';
      p.textContent = 'This chart could not be drawn: ' + err;
      el.appendChild(p);
    });
  }

  window.addEventListener('load', function () {
    draw().then(reportHeight);
    reportHeight();
  });
  // A <details> opening changes the document height; so does a theme switch.
  new ResizeObserver(reportHeight).observe(document.body);
  media.addEventListener('change', draw);
})();
"""
