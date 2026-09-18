"""Dashboard specs, the Vega-Lite they compile to, and the embed contract.

Two things here are load-bearing and neither is visible in a rendered picture,
which is why they are asserted rather than eyeballed:

  - **The embed headers.** Open WebUI shows a tool result as an iframe only when
    the response carries `Content-Type: text/html` *and*
    `Content-Disposition: inline`. Lose either and the dashboard silently
    becomes a wall of markup in the transcript.
  - **Field escaping.** Every Cube column contains a dot, and Vega-Lite reads an
    unescaped dot as nested-object access — so the chart renders empty, with no
    error anywhere.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from hub_api.main import app
from ohdp_agent.cube import CubeError
from ohdp_agent.dashboard import (
    ChartData,
    DashboardSpec,
    bind_data,
    render_html,
    resolve_column,
)

SPEC: dict[str, Any] = {
    "name": "ed-visits",
    "title": "ED visit share",
    "query": {
        "measures": ["ed_visits.avg_percent"],
        "time_dimensions": [{"dimension": "ed_visits.week_end", "granularity": "week"}],
    },
    "vega": {
        "mark": "line",
        "encoding": {
            "x": {"field": "ed_visits.week_end", "type": "temporal"},
            "y": {"field": "ed_visits.avg_percent", "type": "quantitative"},
        },
    },
}

ROWS = [
    {"ed_visits.week_end.week": "2026-01-05", "ed_visits.avg_percent": 3.1},
    {"ed_visits.week_end.week": "2026-01-12", "ed_visits.avg_percent": 3.6},
]


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _spec() -> DashboardSpec:
    return DashboardSpec.model_validate(SPEC)


# --- the spec itself ------------------------------------------------------


def test_yaml_round_trips() -> None:
    spec = _spec()
    assert DashboardSpec.from_yaml(spec.to_yaml()) == spec


def test_yaml_omits_unset_defaults() -> None:
    """A committed file should read as what someone chose, not as every field."""
    text = _spec().to_yaml()
    assert "filters: []" not in text
    assert "order: {}" not in text


def test_a_vega_spec_may_not_carry_literal_data() -> None:
    """`data.values` is the hole a `vega` passthrough would open.

    Literal `values` in a `data` block are smuggled numbers — the chart claims
    its rows came from Cube while drawing whatever the model wrote — so they
    are rejected at validation. Blocks that are only a remote `url` reference
    (a choropleth's geometry) pass.
    """
    with pytest.raises(ValidationError, match="must not carry literal `data` values"):
        DashboardSpec.model_validate(
            {
                **SPEC,
                "vega": {
                    **SPEC["vega"],
                    "data": {"values": [{"ed_visits.avg_percent": 7}]},
                },
            }
        )


def test_a_remote_data_url_is_allowed() -> None:
    """A choropleth needs its geometry; a url-only `data` block is fine."""
    spec = DashboardSpec.model_validate(
        {
            **SPEC,
            "vega": {
                **SPEC["vega"],
                "data": {
                    "url": "https://cdn.jsdelivr.net/npm/us-atlas@3/counties-10m.json",
                    "format": {"type": "topojson", "feature": "states"},
                },
            },
        }
    )
    assert "url" in spec.vega["data"]


def test_an_empty_data_values_placeholder_is_allowed() -> None:
    """`{"values": []}` carries no data — it's how a `lookup`'s `from.data`
    (which `data_path` will point at) stays valid Vega-Lite before binding."""
    spec = DashboardSpec.model_validate(
        {
            **SPEC,
            "data_path": "$.transform[0].from.data.values",
            "vega": {
                **SPEC["vega"],
                "transform": [
                    {
                        "lookup": "id",
                        "from": {
                            "data": {"values": []},
                            "key": "id",
                            "fields": ["ed_visits.avg_percent"],
                        },
                    }
                ],
            },
        }
    )
    assert spec.vega["transform"][0]["from"]["data"] == {"values": []}


def test_a_nested_literal_data_block_is_rejected_too() -> None:
    """The firewall recurses: `values` under `layer` is still a smuggled answer."""
    with pytest.raises(ValidationError, match="must not carry literal `data` values"):
        DashboardSpec.model_validate(
            {
                **SPEC,
                "vega": {
                    "layer": [
                        {
                            "mark": "line",
                            "encoding": {"y": {"field": "ed_visits.avg_percent", "type": "quantitative"}},
                            "data": {"values": [{"ed_visits.avg_percent": 7}]},
                        }
                    ]
                },
            }
        )


def test_transform_and_params_are_allowed() -> None:
    """The model may reach for `transform` and `params`; only `data` is refused."""
    spec = DashboardSpec.model_validate(
        {
            **SPEC,
            "vega": {
                "transform": [{"filter": "datum['ed_visits.avg_percent'] > 0"}],
                "params": [{"name": "cutoff", "value": 0}],
                "mark": "line",
                "encoding": {
                    "x": {"field": "ed_visits.week_end", "type": "temporal"},
                    "y": {"field": "ed_visits.avg_percent", "type": "quantitative"},
                },
            },
        }
    )
    assert spec.vega["transform"][0]["filter"] == "datum['ed_visits.avg_percent'] > 0"


# --- binding rows to the authored Vega-Lite --------------------------------


def test_cube_columns_are_escaped_for_vega() -> None:
    """The silent-empty-chart bug: an unescaped dot is a nested-field lookup."""
    spec = bind_data(_spec().vega, ROWS, list(ROWS[0]))
    assert spec["encoding"]["y"]["field"] == "ed_visits\\.avg_percent"


def test_data_path_injects_rows_at_the_lookup_source() -> None:
    """The Vega-Lite choropleth shape: geometry is the top-level `data.url`,
    and the Cube rows land at `transform.0.from.data.values`. The lookup's
    `from.key`/`from.fields` also get resolved and escaped, same as `field` —
    and so must the joined properties' name (`as`), or the encoding that reads
    a full `cube.member` name back out sees a nested path where the lookup
    wrote a literal dotted key, and every value comes back NaN."""
    rows = [{"ed_visits.fips": "06", "ed_visits.avg_percent": 3.1}]
    spec = DashboardSpec.model_validate(
        {
            **SPEC,
            "data_path": "$.transform[0].from.data.values",
            "vega": {
                "width": 500,
                "height": 300,
                "data": {
                    "url": "https://cdn.jsdelivr.net/npm/us-atlas@3/counties-10m.json",
                    "format": {"type": "topojson", "feature": "states"},
                },
                "transform": [
                    {
                        "lookup": "id",
                        "from": {
                            "data": {"values": []},
                            "key": "ed_visits.fips",
                            "fields": ["ed_visits.avg_percent"],
                        },
                    }
                ],
                "projection": {"type": "albersUsa"},
                "mark": "geoshape",
                "encoding": {
                    "color": {"field": "ed_visits.avg_percent", "type": "quantitative"}
                },
            },
        }
    )
    bound = bind_data(spec.vega, rows, list(rows[0]), spec.data_path)
    assert bound["data"]["url"]  # the geometry reference survives
    assert "values" not in bound["data"]
    from_clause = bound["transform"][0]["from"]
    assert from_clause["data"] == {"values": rows}
    assert from_clause["key"] == "ed_visits\\.fips"
    assert from_clause["fields"] == ["ed_visits\\.avg_percent"]
    # The joined property's name and the encoding that reads it must agree.
    # `as` belongs on the transform itself, beside `lookup`/`from` — Vega-Lite
    # has no `from.as` — not nested inside `from_clause`.
    assert bound["transform"][0]["as"] == ["ed_visits\\.avg_percent"]
    assert "as" not in from_clause
    assert bound["encoding"]["color"]["field"] == "ed_visits\\.avg_percent"


def test_a_geojson_feature_property_is_not_mistaken_for_a_cube_column() -> None:
    """The reported bug: `properties.name` — a GeoJSON feature's own attribute,
    on the choropleth's base `data`, not a joined Cube column at all — matches
    the same `cube.field`-shaped regex a real member does, and resolving it
    against the query's columns always fails since no cube is named
    `properties`. It must be left untouched, the same as `datum.`/`parent.`."""
    rows = [{"ed_visits.state_name": "Ohio", "ed_visits.avg_percent": 3.1}]
    vega = {
        "data": {"url": "https://example.com/us-states.json", "format": {"type": "json"}},
        "transform": [
            {
                "lookup": "properties.name",
                "from": {
                    "data": {"values": []},
                    "key": "ed_visits.state_name",
                    "fields": ["ed_visits.avg_percent"],
                },
            }
        ],
        "mark": "geoshape",
        "encoding": {"color": {"field": "ed_visits.avg_percent", "type": "quantitative"}},
        "tooltip": [
            {"field": "properties.name", "type": "nominal"},
            {"field": "ed_visits.avg_percent", "type": "quantitative"},
        ],
    }
    bound = bind_data(vega, rows, list(rows[0]), "$.transform[0].from.data.values")
    assert bound["transform"][0]["lookup"] == "properties.name"
    assert bound["tooltip"][0]["field"] == "properties.name"
    assert bound["tooltip"][1]["field"] == "ed_visits\\.avg_percent"


def test_a_lookup_against_a_remote_basemap_is_left_alone() -> None:
    """The reported bug, the other direction: Cube rows are the *primary*
    `data` (bound at the default `$.data.values`), and a `lookup` pulls
    geometry in from a remote basemap instead of the other way around. That
    `from.key`/`from.fields` name a column on the *basemap* — a TopoJSON
    feature's `id`/`properties` — never a Cube column, so resolving them
    against `columns` always fails, the same failure mode as `properties.name`
    in `encoding`, just one level deeper. `from.data` carrying a `url` is what
    tells the two kinds of lookup apart."""
    rows = [{"ed_visits.state_name": "Ohio", "ed_visits.avg_percent": 3.1}]
    vega = {
        "data": {"values": []},
        "transform": [
            {
                "lookup": "state_name",
                "from": {
                    "data": {
                        "url": "https://cdn.jsdelivr.net/npm/vega-datasets@v2.9.1/data/us-10m.json",
                        "format": {"type": "topojson", "feature": "states"},
                    },
                    "key": "properties.name",
                    "fields": ["id", "properties"],
                },
                "as": ["geo"],
            }
        ],
        "mark": "geoshape",
        "encoding": {
            "geometry": {"field": "geo", "type": "geojson"},
            "color": {"field": "ed_visits.avg_percent", "type": "quantitative"},
        },
    }
    bound = bind_data(vega, rows, list(rows[0]))
    from_clause = bound["transform"][0]["from"]
    assert from_clause["key"] == "properties.name"
    assert from_clause["fields"] == ["id", "properties"]
    assert bound["transform"][0]["as"] == ["geo"]
    assert bound["encoding"]["color"]["field"] == "ed_visits\\.avg_percent"


def test_bare_lookup_names_resolve_and_keep_the_models_own_field_names() -> None:
    """The reported bug: `from.key: fips` / `from.fields: [avg_coverage_pct, geography]`
    used bare names instead of the full `cube.member` paths Cube actually returns.
    Bare names now resolve by suffix match, same as a granularity suffix does by
    prefix, and — since the model gave no `as` — the joined property keeps the
    bare name it already used in `encoding`/`tooltip`, so those need no change."""
    rows = [
        {
            "immunization.geography": "California",
            "immunization.fips": "06",
            "immunization.avg_coverage_pct": 55.3,
        }
    ]
    vega = {
        "data": {"url": "https://example.com/us.json", "format": {"type": "topojson"}},
        "transform": [
            {
                "lookup": "id",
                "from": {
                    "data": {"values": []},
                    "key": "fips",
                    "fields": ["avg_coverage_pct", "geography"],
                },
            }
        ],
        "mark": "geoshape",
        "encoding": {"color": {"field": "avg_coverage_pct", "type": "quantitative"}},
    }
    bound = bind_data(vega, rows, list(rows[0]), "$.transform[0].from.data.values")
    from_clause = bound["transform"][0]["from"]
    assert from_clause["key"] == "immunization\\.fips"
    assert from_clause["fields"] == ["immunization\\.avg_coverage_pct", "immunization\\.geography"]
    # `as` is a sibling of `lookup`/`from` on the transform, not nested in `from`.
    assert bound["transform"][0]["as"] == ["avg_coverage_pct", "geography"]
    assert "as" not in from_clause
    assert bound["encoding"]["color"]["field"] == "avg_coverage_pct"


def test_an_unresolvable_lookup_key_fails_with_the_reason() -> None:
    """A name matching no column at all — a genuine typo — still raises."""
    rows = [{"ed_visits.fips": "06"}]
    vega = {
        "data": {"url": "https://example.com/us.json", "format": {"type": "topojson"}},
        "transform": [{"lookup": "id", "from": {"key": "not_a_real_column", "fields": []}}],
        "mark": "geoshape",
    }
    with pytest.raises(ValueError, match="column 'not_a_real_column' is not in the query result"):
        bind_data(vega, rows, list(rows[0]), "$.transform[0].from.data.values")


def test_a_lookup_without_fields_fails_with_the_reason() -> None:
    """The reported bug: `from.key` with no `from.fields` at all — the model
    meant to pull individual columns back out with a later `calculate`, but an
    omitted `fields` tells Vega-Lite to attach the *whole* matched row as one
    nested object instead. Nothing downstream can reach a column by name out
    of that, so every value comes back NaN with no error anywhere — unless
    this is caught here first."""
    rows = [
        {
            "chronic_disease.state_name": "Ohio",
            "chronic_disease.avg_age_adjusted_prevalence": 33.4,
        }
    ]
    vega = {
        "data": {"url": "https://example.com/us-states.json", "format": {"type": "topojson"}},
        "transform": [
            {"lookup": "properties.name", "from": {"data": {"values": []}, "key": "state_name"}}
        ],
        "mark": "geoshape",
    }
    with pytest.raises(ValueError, match="from.fields"):
        bind_data(vega, rows, list(rows[0]), "$.transform[0].from.data.values")


def test_a_vega_transform_in_a_vega_lite_spec_fails_with_the_reason() -> None:
    """The reported bug: `{"type": "formula", "expr": ...}` is low-level Vega.
    Vega-Lite identifies a step by its own key and has no `type` discriminator,
    so vega-lite rejects the step in the browser and the card comes back empty
    — with a 200 on this side, where nothing is left to notice it."""
    vega = {
        "transform": [
            {"type": "formula", "expr": "+datum.rate", "as": "rate_num"},
        ],
        "mark": "bar",
        "encoding": {"y": {"field": "rate_num", "type": "quantitative"}},
    }
    with pytest.raises(ValueError, match="names no Vega-Lite transform"):
        bind_data(vega, ROWS, list(ROWS[0]))


def test_the_vega_lite_transforms_a_chart_actually_uses_are_accepted() -> None:
    """The guard must not fail a spec that would have drawn: a false reject
    here costs a chart, which is worse than the silent blank it prevents."""
    vega = {
        "transform": [
            {"calculate": "datum['ed_visits.avg_percent'] * 2", "as": "doubled"},
            {"filter": "datum.doubled > 0"},
            {"window": [{"op": "rank", "as": "r"}], "sort": [{"field": "doubled"}]},
            {"timeUnit": "year", "field": "ed_visits.week_end", "as": "yr"},
            {"joinaggregate": [{"op": "mean", "field": "doubled", "as": "avg"}]},
        ],
        "mark": "bar",
        "encoding": {"y": {"field": "doubled", "type": "quantitative"}},
    }
    bind_data(vega, ROWS, list(ROWS[0]))


def test_a_data_path_that_leads_nowhere_fails_with_the_reason() -> None:
    with pytest.raises(ValueError, match="does not name an existing part"):
        bind_data(_spec().vega, ROWS, list(ROWS[0]), "$.transform[0].from.data.values")


def test_an_invalid_data_path_is_rejected_at_validation() -> None:
    with pytest.raises(ValidationError, match="valid JSONPath"):
        DashboardSpec.model_validate({**SPEC, "data_path": "not a jsonpath"})


def test_data_is_bound_as_values_not_a_url() -> None:
    """The rows land as `data.values`, never a URL the browser would fetch."""
    spec = bind_data(_spec().vega, ROWS, list(ROWS[0]))
    assert spec["data"] == {"values": ROWS}
    assert "url" not in spec["data"]


def test_a_unit_spec_gets_width_and_autosize_defaults() -> None:
    """A bare {mark, encoding} still renders full-width, like the compiled output."""
    spec = bind_data(_spec().vega, ROWS, list(ROWS[0]))
    assert spec["width"] == "container"
    assert spec["autosize"] == {"type": "fit", "contains": "padding"}


def test_a_composite_spec_is_left_to_size_itself() -> None:
    """`width: container` would fight a `layer`; composites size their own children."""
    layer = {
        "layer": [
            {
                "mark": "line",
                "encoding": {"y": {"field": "ed_visits.avg_percent", "type": "quantitative"}},
            }
        ]
    }
    spec = bind_data(layer, ROWS, list(ROWS[0]))
    assert "width" not in spec
    assert "autosize" not in spec
    assert spec["data"] == {"values": ROWS}


def test_a_transform_output_is_not_escaped() -> None:
    """A dotless field (a transform output) is left alone; only members are escaped."""
    vega = {
        "transform": [{"calculate": "datum['ed_visits.avg_percent']", "as": "rate"}],
        "mark": "bar",
        "encoding": {
            "x": {"field": "ed_visits.week_end", "type": "temporal"},
            "y": {"field": "rate", "type": "quantitative"},
        },
    }
    spec = bind_data(vega, ROWS, list(ROWS[0]))
    assert spec["encoding"]["y"]["field"] == "rate"
    assert spec["encoding"]["x"]["field"] == "ed_visits\\.week_end\\.week"


def test_a_time_dimension_resolves_through_its_granularity_suffix() -> None:
    """Cube keys a granular time dimension `cube.field.week`, not `cube.field`."""
    assert resolve_column("ed_visits.week_end", list(ROWS[0])) == "ed_visits.week_end.week"


def test_an_unknown_column_names_the_ones_that_exist() -> None:
    """The error goes back to the model, which is the thing that can fix it."""
    with pytest.raises(ValueError, match="ed_visits.avg_percent"):
        resolve_column("ed_visits.nope", list(ROWS[0]))


# --- the rendered page -----------------------------------------------------


def test_render_carries_its_own_source_and_disclaimer() -> None:
    html = render_html(_spec(), ChartData(rows=ROWS, row_count=2))
    assert "ed-visits.yaml" in html
    assert "Not clinical decision support" in html
    # Without this the iframe renders at a stub height and the content is cut off.
    assert "iframe:height" in html


def test_an_unplottable_spec_raises_rather_than_rendering_silently() -> None:
    """`render_html` must not swallow the error into a 200 embed.

    Open WebUI hands the model a fixed "active and visible" string for any
    embed, success or not, so a caught-and-printed error here would be
    invisible to the model — see `test_an_unplottable_spec_returns_an_error`
    for the route-level contract this protects.
    """
    spec = DashboardSpec.model_validate(
        {
            **SPEC,
            "vega": {
                "mark": "line",
                "encoding": {
                    "x": {"field": "ed_visits.absent", "type": "temporal"},
                    "y": {"field": "ed_visits.avg_percent", "type": "quantitative"},
                },
            },
        }
    )
    with pytest.raises(ValueError, match="ed_visits.avg_percent"):
        render_html(spec, ChartData(rows=ROWS, row_count=2))


def test_row_data_is_escaped_into_the_payload_script() -> None:
    """A value cannot close the script element it is embedded in."""
    rows = [{"ed_visits.week_end.week": "</script><script>x", "ed_visits.avg_percent": 1.0}]
    html = render_html(_spec(), ChartData(rows=rows, row_count=1))
    assert "</script><script>x" not in html
    assert "\\u003c/script" in html


def test_the_spec_has_nowhere_to_put_rows() -> None:
    """The property behind "the model supplies a `vega` spec, never data values".

    It is structural rather than a matter of discipline: a spec is a query plus
    a `vega` spec, and there is no field a caller can use to smuggle numbers
    past Cube — not as a `rows` key (rejected by `extra="forbid"`) and not as a
    `data` key inside `vega` (rejected by the `VegaSpec` validator). It is also
    what keeps a saved chart from going stale — there is no cached answer in the
    file to go stale.
    """
    with pytest.raises(ValidationError):
        DashboardSpec.model_validate({**SPEC, "rows": [{"ed_visits.avg_percent": 99}]})


# --- the embed contract ----------------------------------------------------


def test_render_requires_a_credential(client: TestClient) -> None:
    assert client.post("/tools/render_dashboard", json=SPEC).status_code == 401


def test_the_spec_advertises_html_for_the_embed(client: TestClient) -> None:
    """Open WebUI needs `text/html` on the response to treat it as an embed."""
    spec = client.get("/tools/openapi.json").json()
    content = spec["paths"]["/render_dashboard"]["post"]["responses"]["200"]["content"]
    assert "text/html" in content


def test_a_successful_render_returns_an_embeddable_response(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The contract with Open WebUI, asserted rather than eyeballed.

    `process_tool_result` only turns an external tool's result into an iframe
    when both headers are right. Lose either and the dashboard degrades into
    several kilobytes of markup pasted into the conversation — which renders as
    text, so nothing raises and nothing fails.
    """

    async def fake_query(self: object, query: object) -> dict[str, Any]:
        return {"rows": ROWS, "row_count": len(ROWS), "truncated": False, "applied_limit": 1000}

    monkeypatch.setattr("ohdp_agent.cube.CubeClient.run_metric_query", fake_query)

    response = client.post(
        "/tools/render_dashboard", json=SPEC, headers={"X-Forwarded-Email": "a@b.test"}
    )

    assert response.status_code == 200
    assert response.headers["content-disposition"] == "inline"
    assert response.headers["content-type"].startswith("text/html")
    assert "vegaEmbed" in response.text


def test_a_rejected_query_returns_cubes_own_reason(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cube names the wrong member; the model can fix its own spec from that."""

    async def fail(self: object, query: object) -> dict[str, Any]:
        raise CubeError("Member 'ed_visits.nope' not found")

    monkeypatch.setattr("ohdp_agent.cube.CubeClient.run_metric_query", fail)

    response = client.post(
        "/tools/render_dashboard", json=SPEC, headers={"X-Forwarded-Email": "a@b.test"}
    )
    assert response.status_code == 400
    assert "ed_visits.nope" in response.json()["detail"]


def test_an_unplottable_spec_returns_an_error_not_an_embed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A spec/column mismatch must fail the tool call, not render a 200 embed.

    Open WebUI gives the model a fixed "active and visible" string for *any*
    embed — so a 200 here, even with an in-page error paragraph, would look
    like success to the model and it would never retry with a fixed spec.
    """
    bad_spec = {
        **SPEC,
        "vega": {
            "mark": "line",
            "encoding": {
                "x": {"field": "ed_visits.absent", "type": "temporal"},
                "y": {"field": "ed_visits.avg_percent", "type": "quantitative"},
            },
        },
    }

    async def fake_query(self: object, query: object) -> dict[str, Any]:
        return {"rows": ROWS, "row_count": len(ROWS), "truncated": False, "applied_limit": 1000}

    monkeypatch.setattr("ohdp_agent.cube.CubeClient.run_metric_query", fake_query)

    response = client.post(
        "/tools/render_dashboard", json=bad_spec, headers={"X-Forwarded-Email": "a@b.test"}
    )
    assert response.status_code == 422
    assert "ed_visits.avg_percent" in response.json()["detail"]
    assert response.headers.get("content-disposition") != "inline"


# --- the handoff to Open WebUI's frontend ----------------------------------
#
# The embed does not travel to the browser as an HTTP body. Open WebUI puts it
# on a `function_call_output` item, `structuredOutput.ts` JSON-stringifies it
# into a token attribute, and `ToolCallDisplay.svelte` /
# `ConsecutiveDetailsGroup.svelte` then run `parseJSONString(decode(attr))` —
# an HTML-entity decode *before* the JSON parse. These two tests replay that
# round trip, because everything it can break, it breaks silently: their
# `parseJSONString` returns the raw string rather than raising, and the
# `Array.isArray(...)` guard drops the embed with no error on any surface.


def _through_open_webui(document: str) -> Any:
    """What the frontend ends up with, given `document` as the embed."""
    import html as html_module

    attribute = json.dumps([document])  # stringifyAttribute()
    decoded = html_module.unescape(attribute)  # decode() from html-entities
    try:
        return json.loads(decoded)  # parseJSONString()
    except json.JSONDecodeError:
        return decoded  # their catch: the raw string, not an array


def test_the_embed_survives_decode_before_parse() -> None:
    """The bug that made the dashboard silently not appear at all.

    `html.escape(..., quote=True)` emits `&quot;` for every quote in the
    embedded Cube query and YAML source. The decode pass turns each one into a
    bare `"` inside a JSON string literal, and the whole array stops parsing.
    """
    spec = DashboardSpec.model_validate(SPEC)
    document = render_html(spec, ChartData(rows=ROWS, row_count=2))

    assert "&quot;" not in document
    survived = _through_open_webui(document)
    assert isinstance(survived, list), "the embed was dropped before it reached the iframe"
    assert survived[0] == document


def test_markup_in_a_title_cannot_survive_the_decode_as_markup() -> None:
    """The other half: that decode also *undoes* a single level of escaping.

    Titles and captions are model-supplied and cell values come from the
    warehouse, so escaping them once and letting the host decode it would hand
    the frontend live markup.
    """
    spec = DashboardSpec.model_validate(
        {**SPEC, "title": "<script>alert(1)</script>"}
    )
    document = render_html(spec, ChartData(rows=ROWS, row_count=2))

    delivered = _through_open_webui(document)
    assert isinstance(delivered, list)
    assert "<script>alert(1)</script>" not in delivered[0]
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in delivered[0]
