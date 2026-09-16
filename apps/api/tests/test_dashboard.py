"""Dashboard specs, the Vega-Lite they compile to, and the embed contract.

Three things here are load-bearing and none of them are visible in a rendered
picture, which is why they are asserted rather than eyeballed:

  - **The embed headers.** Open WebUI shows a tool result as an iframe only when
    the response carries `Content-Type: text/html` *and*
    `Content-Disposition: inline`. Lose either and the dashboard silently
    becomes a wall of markup in the transcript.
  - **Field escaping.** Every Cube column contains a dot, and Vega-Lite reads an
    unescaped dot as nested-object access — so the chart renders empty, with no
    error anywhere.
  - **Every committed spec parses.** A saved dashboard that no longer validates
    should fail here, not in front of a user.
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
    Chart,
    DashboardSpec,
    PanelData,
    build_vega_spec,
    render_html,
    resolve_column,
)
from ohdp_agent.dashboards import list_saved, load_saved

SPEC: dict[str, Any] = {
    "name": "ed-visits",
    "title": "ED visit share",
    "panels": [
        {
            "title": "By week",
            "query": {
                "measures": ["ed_visits.avg_percent"],
                "time_dimensions": [{"dimension": "ed_visits.week_end", "granularity": "week"}],
            },
            "chart": {
                "type": "line",
                "x": "ed_visits.week_end",
                "y": "ed_visits.avg_percent",
            },
        }
    ],
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


def test_bar_chart_refuses_a_quantitative_category_axis() -> None:
    """The reversed-axis mistake, which otherwise draws a wrong chart silently.

    `horizontal_bar` swaps the axes on screen but is still authored category-in-
    `x`, measure-in-`y`; a model that reverses them produces something that
    renders and means nothing. This is the shape that mistake takes.
    """
    with pytest.raises(ValidationError, match="x_type cannot be 'quantitative'"):
        Chart(type="horizontal_bar", x="c.value", y="c.state", x_type="quantitative")


def test_point_chart_still_allows_a_quantitative_x() -> None:
    """A scatter is the form where a quantitative x is the whole point."""
    assert Chart(type="point", x="c.age", y="c.rate", x_type="quantitative").x == "c.age"


def test_arbitrary_vega_lite_is_not_accepted() -> None:
    """`Chart` is a closed enum, not a Vega-Lite passthrough.

    An arbitrary spec would carry `data.url`, which fetches from inside the
    viewer's browser — the reason this layer compiles the spec instead of
    forwarding one.
    """
    with pytest.raises(ValidationError):
        Chart.model_validate(
            {"type": "line", "x": "c.a", "y": "c.b", "data": {"url": "https://example.com/x.json"}}
        )


# --- compiling to Vega-Lite ------------------------------------------------


def test_cube_columns_are_escaped_for_vega() -> None:
    """The silent-empty-chart bug: an unescaped dot is a nested-field lookup."""
    spec = build_vega_spec(_spec().panels[0].chart, ROWS, list(ROWS[0]))
    assert spec["encoding"]["y"]["field"] == "ed_visits\\.avg_percent"


def test_a_time_dimension_resolves_through_its_granularity_suffix() -> None:
    """Cube keys a granular time dimension `cube.field.week`, not `cube.field`."""
    assert resolve_column("ed_visits.week_end", list(ROWS[0])) == "ed_visits.week_end.week"


def test_an_unknown_column_names_the_ones_that_exist() -> None:
    """The error goes back to the model, which is the thing that can fix it."""
    with pytest.raises(ValueError, match="ed_visits.avg_percent"):
        resolve_column("ed_visits.nope", list(ROWS[0]))


def test_bars_include_zero_and_lines_do_not() -> None:
    """A bar's length is the value; a line's position is. Only one must hold zero."""
    line = build_vega_spec(Chart(type="line", x="c.week", y="c.rate"), [], ["c.week", "c.rate"])
    bar = build_vega_spec(Chart(type="bar", x="c.state", y="c.rate"), [], ["c.state", "c.rate"])
    assert line["encoding"]["y"]["scale"]["zero"] is False
    assert bar["encoding"]["y"]["scale"]["zero"] is True


def test_horizontal_bar_puts_the_measure_on_the_x_axis() -> None:
    """Authored category-in-`x`; only the screen assignment swaps."""
    spec = build_vega_spec(
        Chart(type="horizontal_bar", x="c.state", y="c.rate"), [], ["c.state", "c.rate"]
    )
    assert spec["encoding"]["x"]["field"] == "c\\.rate"
    assert spec["encoding"]["y"]["field"] == "c\\.state"


def test_a_colour_encoding_always_gets_a_legend() -> None:
    """Two or more series must never be identified by colour alone."""
    spec = build_vega_spec(
        Chart(type="line", x="c.week", y="c.rate", color="c.pathogen"),
        [],
        ["c.week", "c.rate", "c.pathogen"],
    )
    assert spec["encoding"]["color"]["legend"]["orient"] == "bottom"


# --- the rendered page -----------------------------------------------------


def test_render_carries_its_own_source_and_disclaimer() -> None:
    html = render_html(_spec(), [PanelData(rows=ROWS, row_count=2)])
    assert "ed-visits.yaml" in html
    assert "Not clinical decision support" in html
    # Without this the iframe renders at a stub height and the content is cut off.
    assert "iframe:height" in html


def test_a_broken_panel_does_not_lose_the_others() -> None:
    spec = DashboardSpec.model_validate(
        {
            **SPEC,
            "panels": [
                SPEC["panels"][0],
                {
                    **SPEC["panels"][0],
                    "title": "Broken",
                    "chart": {
                        "type": "line",
                        "x": "ed_visits.absent",
                        "y": "ed_visits.avg_percent",
                    },
                },
            ],
        }
    )
    html = render_html(spec, [PanelData(rows=ROWS, row_count=2)] * 2)
    assert "could not be drawn" in html
    assert "By week" in html


def test_row_data_is_escaped_into_the_payload_script() -> None:
    """A value cannot close the script element it is embedded in."""
    rows = [{"ed_visits.week_end.week": "</script><script>x", "ed_visits.avg_percent": 1.0}]
    html = render_html(_spec(), [PanelData(rows=rows, row_count=1)])
    assert "</script><script>x" not in html
    assert "\\u003c/script" in html


# --- the saved ones --------------------------------------------------------


def test_every_committed_dashboard_parses() -> None:
    """CI, not a user, is where a broken saved spec should surface."""
    saved = list_saved()
    assert saved, "expected at least the worked example in ohdp_agent/dashboards/"
    for spec in saved:
        assert load_saved(spec.name) == spec


def test_a_panel_has_nowhere_to_put_rows() -> None:
    """The property behind "the model supplies encodings, never data values".

    It is structural rather than a matter of discipline: a panel is a query plus
    an encoding, and `extra="forbid"` means there is no field a caller can use
    to smuggle numbers past Cube. It is also what keeps a saved dashboard from
    going stale — there is no cached answer in the file to go stale.
    """
    with pytest.raises(ValidationError):
        DashboardSpec.model_validate(
            {**SPEC, "panels": [{**SPEC["panels"][0], "rows": [{"ed_visits.avg_percent": 99}]}]}
        )


def test_load_saved_does_not_build_a_path_from_its_argument() -> None:
    """`name` comes from a model; it never reaches the filesystem."""
    with pytest.raises(KeyError):
        load_saved("../../../etc/passwd")


# --- the embed contract ----------------------------------------------------


def test_open_saved_dashboard_404s_with_the_available_names(client: TestClient) -> None:
    response = client.get(
        "/tools/saved_dashboards/does-not-exist", headers={"X-Forwarded-Email": "a@b.test"}
    )
    assert response.status_code == 404
    assert "respiratory-season" in response.json()["detail"]


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


def test_a_rejected_panel_query_returns_cubes_own_reason(
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
    document = render_html(spec, [PanelData(rows=ROWS, row_count=2)])

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
        {**SPEC, "panels": [{**SPEC["panels"][0], "title": "<script>alert(1)</script>"}]}
    )
    document = render_html(spec, [PanelData(rows=ROWS, row_count=2)])

    delivered = _through_open_webui(document)
    assert isinstance(delivered, list)
    assert "<script>alert(1)</script>" not in delivered[0]
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in delivered[0]
