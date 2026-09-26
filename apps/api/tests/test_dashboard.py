"""Dashboard specs, the Vega-Lite they compile to, and the embed contract.

Two things here are load-bearing and neither is visible in a rendered picture,
which is why they are asserted rather than eyeballed:

  - **The embed headers.** The Hub UI shows a tool result as an iframe only when
    the response carries `Content-Type: text/html` *and*
    `Content-Disposition: inline`. Lose either and the dashboard silently
    becomes a wall of markup in the transcript.
  - **Field escaping.** Every Cube column contains a dot, and Vega-Lite reads an
    unescaped dot as nested-object access — so the chart renders empty, with no
    error anywhere.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from hub_api import issues
from hub_api.main import app
from ohdp_agent.cube import CubeError
from ohdp_agent.dashboard import (
    DEFAULT_DATA_PATH,
    MAX_CHART_HEIGHT,
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
    "vega_lite": {
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


@pytest.fixture
def authenticated_client() -> Iterator[TestClient]:
    """A browser's identity comes from the hub's own signed session cookie
    (hub_api.auth), not a header — dependency_overrides is the same
    substitute test_chat_threads.py uses for chat.get_user_email. Tests that
    don't exercise identity just need a "web" caller past get_reporter.
    `tools_app` is its own FastAPI instance (mounted, not included), so the
    override belongs on it, not on `app`."""
    issues.tools_app.dependency_overrides[issues.get_reporter] = lambda: issues.Reporter(
        source="web", email="a@b.test"
    )
    try:
        yield TestClient(app)
    finally:
        issues.tools_app.dependency_overrides.clear()


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
    """`data.values` is the hole a `vega_lite` passthrough would open.

    Literal `values` in a `data` block are smuggled numbers — the chart claims
    its rows came from Cube while drawing whatever the model wrote — so they
    are rejected at validation. Blocks that are only a remote `url` reference
    (a choropleth's geometry) pass.
    """
    with pytest.raises(ValidationError, match="must not carry literal `data` values"):
        DashboardSpec.model_validate(
            {
                **SPEC,
                "vega_lite": {
                    **SPEC["vega_lite"],
                    "data": {"values": [{"ed_visits.avg_percent": 7}]},
                },
            }
        )


def test_a_remote_data_url_is_allowed() -> None:
    """A choropleth needs its geometry; a url-only `data` block is fine."""
    spec = DashboardSpec.model_validate(
        {
            **SPEC,
            "vega_lite": {
                **SPEC["vega_lite"],
                "data": {
                    "url": "https://cdn.jsdelivr.net/npm/us-atlas@3/counties-10m.json",
                    "format": {"type": "topojson", "feature": "states"},
                },
            },
        }
    )
    assert "url" in spec.vega_lite["data"]


def test_an_empty_data_values_placeholder_is_allowed() -> None:
    """`{"values": []}` carries no data — it's how a `lookup`'s `from.data`
    (which `data_path` will point at) stays valid Vega-Lite before binding."""
    spec = DashboardSpec.model_validate(
        {
            **SPEC,
            "data_path": "$.transform[0].from.data.values",
            "vega_lite": {
                **SPEC["vega_lite"],
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
    assert spec.vega_lite["transform"][0]["from"]["data"] == {"values": []}


def test_a_nested_literal_data_block_is_rejected_too() -> None:
    """The firewall recurses: `values` under `layer` is still a smuggled answer."""
    with pytest.raises(ValidationError, match="must not carry literal `data` values"):
        DashboardSpec.model_validate(
            {
                **SPEC,
                "vega_lite": {
                    "layer": [
                        {
                            "mark": "line",
                            "encoding": {
                                "y": {
                                    "field": "ed_visits.avg_percent",
                                    "type": "quantitative",
                                }
                            },
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
            "vega_lite": {
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
    assert spec.vega_lite["transform"][0]["filter"] == "datum['ed_visits.avg_percent'] > 0"


# --- binding rows to the authored Vega-Lite --------------------------------


def test_cube_columns_are_escaped_for_vega() -> None:
    """The silent-empty-chart bug: an unescaped dot is a nested-field lookup."""
    spec = bind_data(_spec().vega_lite, ROWS, list(ROWS[0]))
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
            "vega_lite": {
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
                "encoding": {"color": {"field": "ed_visits.avg_percent", "type": "quantitative"}},
            },
        }
    )
    bound = bind_data(spec.vega_lite, rows, list(rows[0]), spec.data_path)
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
    vega_lite = {
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
    bound = bind_data(vega_lite, rows, list(rows[0]), "$.transform[0].from.data.values")
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
    vega_lite = {
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
    bound = bind_data(vega_lite, rows, list(rows[0]))
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
    vega_lite = {
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
    bound = bind_data(vega_lite, rows, list(rows[0]), "$.transform[0].from.data.values")
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
    vega_lite = {
        "data": {"url": "https://example.com/us.json", "format": {"type": "topojson"}},
        "transform": [{"lookup": "id", "from": {"key": "not_a_real_column", "fields": []}}],
        "mark": "geoshape",
    }
    with pytest.raises(ValueError, match="column 'not_a_real_column' is not in the query result"):
        bind_data(vega_lite, rows, list(rows[0]), "$.transform[0].from.data.values")


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
    vega_lite = {
        "data": {"url": "https://example.com/us-states.json", "format": {"type": "topojson"}},
        "transform": [
            {"lookup": "properties.name", "from": {"data": {"values": []}, "key": "state_name"}}
        ],
        "mark": "geoshape",
    }
    with pytest.raises(ValueError, match="from.fields"):
        bind_data(vega_lite, rows, list(rows[0]), "$.transform[0].from.data.values")


_FLU_ROWS = [
    {
        "immunization__coverage.geography": "California",
        "immunization__coverage.fips": "06",
        "immunization__coverage.avg_coverage_pct": 48.2,
    }
]
_FLU_PATH = "$.transform[0].from.data.values"


def _flu_choropleth(from_clause: dict[str, Any], as_: list[str]) -> dict[str, Any]:
    return {
        "data": {
            "url": "https://cdn.jsdelivr.net/npm/us-atlas@3/states-10m.json",
            "format": {"type": "topojson", "feature": "states"},
        },
        "mark": "geoshape",
        "projection": {"type": "albersUsa"},
        "transform": [{"lookup": "id", "from": {"data": {"values": []}, **from_clause}, "as": as_}],
        "encoding": {"color": {"field": "coverage_pct", "type": "quantitative"}},
    }


def test_a_lookup_without_a_key_fails_with_the_reason() -> None:
    """The reported bug: no `from.key` compiles fine in Vega-Lite and then
    throws `reading 'signal'` in the browser's Vega parse."""
    vega_lite = _flu_choropleth(
        {"fields": ["immunization__coverage.avg_coverage_pct"]}, ["coverage_pct"]
    )
    with pytest.raises(ValueError, match="must give `from.key`"):
        bind_data(vega_lite, _FLU_ROWS, list(_FLU_ROWS[0]), _FLU_PATH)


def test_a_lookup_key_listed_in_fields_fails_with_the_reason() -> None:
    """Same report: the FIPS key in `fields` shifted `as` one column off, so
    `coverage_pct` would have held the FIPS code."""
    vega_lite = _flu_choropleth(
        {
            "key": "immunization__coverage.fips",
            "fields": ["immunization__coverage.fips", "immunization__coverage.geography"],
        },
        ["coverage_pct", "state_name"],
    )
    with pytest.raises(ValueError, match="also listed in `from.fields`"):
        bind_data(vega_lite, _FLU_ROWS, list(_FLU_ROWS[0]), _FLU_PATH)


def test_a_lookup_with_mismatched_as_fails_with_the_reason() -> None:
    vega_lite = _flu_choropleth(
        {
            "key": "immunization__coverage.fips",
            "fields": ["immunization__coverage.avg_coverage_pct"],
        },
        ["coverage_pct", "state_name"],
    )
    with pytest.raises(ValueError, match="1 `from.fields`"):
        bind_data(vega_lite, _FLU_ROWS, list(_FLU_ROWS[0]), _FLU_PATH)


def test_the_corrected_flu_choropleth_binds_and_draws() -> None:
    vega_lite = _flu_choropleth(
        {
            "key": "immunization__coverage.fips",
            "fields": [
                "immunization__coverage.avg_coverage_pct",
                "immunization__coverage.geography",
            ],
        },
        ["coverage_pct", "state_name"],
    )
    bound = bind_data(vega_lite, _FLU_ROWS, list(_FLU_ROWS[0]), _FLU_PATH)
    assert bound["transform"][0]["from"]["key"] == "immunization__coverage\\.fips"
    # The basemap is only blanked for the server-side draw, never in what ships.
    assert "url" in bound["data"]


def test_a_spec_vega_cannot_draw_fails_on_the_server() -> None:
    """The catch-all: a lookup against the remote basemap is outside the
    Cube-rows guards, so a missing key there is caught only by drawing it."""
    vega_lite = {
        "transform": [
            {
                "lookup": "ed_visits.state",
                "from": {
                    "data": {"url": "https://example.com/us.json", "format": {"type": "json"}},
                    "fields": ["geometry"],
                },
            }
        ],
        "mark": "bar",
        "encoding": {"y": {"field": "ed_visits.avg_percent", "type": "quantitative"}},
    }
    rows = [{"ed_visits.state": "CA", "ed_visits.avg_percent": 3.1}]
    with pytest.raises(ValueError, match="Vega could not draw this spec: TypeError"):
        bind_data(vega_lite, rows, list(rows[0]))


def test_a_vega_transform_in_a_vega_lite_spec_fails_with_the_reason() -> None:
    """The reported bug: `{"type": "formula", "expr": ...}` is low-level Vega.
    Vega-Lite identifies a step by its own key and has no `type` discriminator,
    so vega-lite rejects the step in the browser and the card comes back empty
    — with a 200 on this side, where nothing is left to notice it."""
    vega_lite = {
        "transform": [
            {"type": "formula", "expr": "+datum.rate", "as": "rate_num"},
        ],
        "mark": "bar",
        "encoding": {"y": {"field": "rate_num", "type": "quantitative"}},
    }
    with pytest.raises(ValueError, match="names no Vega-Lite transform"):
        bind_data(vega_lite, ROWS, list(ROWS[0]))


def test_the_vega_lite_transforms_a_chart_actually_uses_are_accepted() -> None:
    """The guard must not fail a spec that would have drawn: a false reject
    here costs a chart, which is worse than the silent blank it prevents."""
    vega_lite = {
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
    bind_data(vega_lite, ROWS, list(ROWS[0]))


def _choropleth(data_path: str | None = None) -> dict[str, object]:
    """A choropleth spec: geometry as the top-level `data.url`, rows joined in
    by a `lookup` whose `from.data` is the `{"values": []}` placeholder."""
    spec: dict[str, object] = {
        **SPEC,
        "vega_lite": {
            "data": {
                "url": "https://cdn.jsdelivr.net/npm/us-atlas@3/states-10m.json",
                "format": {"type": "topojson", "feature": "states"},
            },
            "transform": [
                {
                    "lookup": "properties.name",
                    "from": {
                        "data": {"values": []},
                        "key": "ed_visits.week_end",
                        "fields": ["ed_visits.avg_percent"],
                    },
                    "as": ["pct"],
                }
            ],
            "projection": {"type": "albersUsa"},
            "mark": "geoshape",
            "encoding": {"color": {"field": "pct", "type": "quantitative"}},
        },
    }
    if data_path is not None:
        spec["data_path"] = data_path
    return spec


def test_rows_are_never_bound_onto_the_basemap_geometry() -> None:
    """A choropleth that forgot `data_path` used to render 200 and then throw in
    the reader's browser: rows written beside the geometry's `url` make
    Vega-Lite read the block as inline data and apply its `topojson` format to
    the Cube rows (`Cannot read properties of undefined (reading 'states')`).
    The failure is client-side, so nothing but this check ever sees it — and the
    message has to name the path that would have worked."""
    spec = DashboardSpec.model_validate(_choropleth())
    assert spec.data_path == DEFAULT_DATA_PATH
    with pytest.raises(ValueError, match="basemap geometry") as excinfo:
        bind_data(spec.vega_lite, ROWS, list(ROWS[0]), spec.data_path)
    assert "'$.transform[0].from.data.values'" in str(excinfo.value)


def test_an_unbound_row_placeholder_fails_rather_than_drawing_empty() -> None:
    """The other half of the same mistake: rows bound somewhere real, but not at
    the placeholder the `lookup` joins from. That join runs against `[]` — valid
    Vega-Lite, a map with every value missing, and no error anywhere."""
    vega_lite = {
        "transform": [
            {
                "lookup": "ed_visits.week_end",
                "from": {
                    "data": {"values": []},
                    "key": "ed_visits.week_end",
                    "fields": ["ed_visits.avg_percent"],
                },
            }
        ],
        "mark": "bar",
        "encoding": {"y": {"field": "ed_visits.avg_percent", "type": "quantitative"}},
    }
    with pytest.raises(ValueError, match="row placeholder") as excinfo:
        bind_data(vega_lite, ROWS, list(ROWS[0]), DEFAULT_DATA_PATH)
    assert "'$.transform[0].from.data.values'" in str(excinfo.value)


def test_a_choropleth_with_the_right_data_path_still_binds() -> None:
    """The checks above must not cost the spec that was written correctly."""
    spec = DashboardSpec.model_validate(_choropleth("$.transform[0].from.data.values"))
    bound = bind_data(spec.vega_lite, ROWS, list(ROWS[0]), spec.data_path)
    assert "values" not in bound["data"]
    assert bound["transform"][0]["from"]["data"] == {"values": ROWS}


def test_a_data_path_that_leads_nowhere_fails_with_the_reason() -> None:
    with pytest.raises(ValueError, match="does not name an existing part"):
        bind_data(_spec().vega_lite, ROWS, list(ROWS[0]), "$.transform[0].from.data.values")


def test_an_invalid_data_path_is_rejected_at_validation() -> None:
    with pytest.raises(ValidationError, match="valid JSONPath"):
        DashboardSpec.model_validate({**SPEC, "data_path": "not a jsonpath"})


def test_data_is_bound_as_values_not_a_url() -> None:
    """The rows land as `data.values`, never a URL the browser would fetch."""
    spec = bind_data(_spec().vega_lite, ROWS, list(ROWS[0]))
    assert spec["data"] == {"values": ROWS}
    assert "url" not in spec["data"]


def test_a_unit_spec_gets_width_and_autosize_defaults() -> None:
    """A bare {mark, encoding} still renders full-width, like the compiled output."""
    spec = bind_data(_spec().vega_lite, ROWS, list(ROWS[0]))
    assert spec["width"] == "container"
    assert spec["autosize"] == {"type": "fit", "contains": "padding"}


def test_a_layered_spec_is_sized_like_a_unit_one() -> None:
    """A `layer` is one view with one set of axes — it takes container width."""
    layer = {
        "layer": [
            {
                "mark": "line",
                "encoding": {"y": {"field": "ed_visits.avg_percent", "type": "quantitative"}},
            }
        ]
    }
    spec = bind_data(layer, ROWS, list(ROWS[0]))
    assert spec["width"] == "container"
    assert spec["autosize"] == {"type": "fit", "contains": "padding"}
    assert spec["data"] == {"values": ROWS}


def test_an_arranging_composite_is_left_to_size_itself() -> None:
    """Vega-Lite has no container width under facet/concat/repeat."""
    faceted = {
        "facet": {"field": "ed_visits.week_end", "type": "nominal"},
        "spec": {
            "mark": "line",
            "encoding": {"y": {"field": "ed_visits.avg_percent", "type": "quantitative"}},
        },
    }
    spec = bind_data(faceted, ROWS, list(ROWS[0]))
    assert "width" not in spec
    assert "autosize" not in spec


def test_an_authored_size_is_replaced_by_the_cards_own() -> None:
    """An 880px chart in a ~700px column is clipped, not scaled — so it is capped."""
    wide = {
        "width": 880,
        "height": 560,
        "mark": "circle",
        "encoding": {"y": {"field": "ed_visits.avg_percent", "type": "quantitative"}},
    }
    spec = bind_data(wide, ROWS, list(ROWS[0]))
    assert spec["width"] == "container"
    assert spec["height"] == MAX_CHART_HEIGHT


def test_a_height_under_the_cap_is_kept() -> None:
    """The cap is a ceiling, not a size: a short chart stays short."""
    short = {
        "height": 120,
        "mark": "line",
        "encoding": {"y": {"field": "ed_visits.avg_percent", "type": "quantitative"}},
    }
    assert bind_data(short, ROWS, list(ROWS[0]))["height"] == 120


def test_a_layer_childs_own_size_is_dropped() -> None:
    """A child's width beats the top level's, so it would undo the cap silently."""
    layer = {
        "layer": [
            {
                "width": 880,
                "height": 560,
                "mark": "line",
                "encoding": {"y": {"field": "ed_visits.avg_percent", "type": "quantitative"}},
            }
        ]
    }
    spec = bind_data(layer, ROWS, list(ROWS[0]))
    assert spec["width"] == "container"
    assert "width" not in spec["layer"][0]
    assert "height" not in spec["layer"][0]


def test_a_transform_output_is_not_escaped() -> None:
    """A dotless field (a transform output) is left alone; only members are escaped."""
    vega_lite = {
        "transform": [{"calculate": "datum['ed_visits.avg_percent']", "as": "rate"}],
        "mark": "bar",
        "encoding": {
            "x": {"field": "ed_visits.week_end", "type": "temporal"},
            "y": {"field": "rate", "type": "quantitative"},
        },
    }
    spec = bind_data(vega_lite, ROWS, list(ROWS[0]))
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


def test_render_carries_its_own_source() -> None:
    """The disclaimer itself moved to `ohdp_agent.loop.DISCLAIMER`, attached to
    the chat turn rather than repeated on every card (`test_loop.py`'s
    `test_disclaimer_is_attached_by_us_not_asked_of_the_model`) — this only
    covers what the card itself still owns.
    """
    html = render_html(_spec(), ChartData(rows=ROWS, row_count=2))
    assert "ed-visits.yaml" in html
    # Without this the iframe renders at a stub height and the content is cut off.
    assert "iframe:height" in html


def test_the_page_never_styles_a_bare_table_cell() -> None:
    """The reported bug: the hover tooltip's text was unreadable in dark mode.

    vega-tooltip appends its own <table> to <body>, outside `.panel`, so a bare
    `td { color: ... }` here matches the tooltip's value cells directly — and a
    direct match beats the colour those cells merely inherit from
    `#vg-tooltip-element`. Dark mode's near-white `--text` then landed on the
    tooltip's own pale background. The page's table styles must stay anchored
    to `.panel`; nothing renders a bare cell selector.
    """
    html = render_html(_spec(), ChartData(rows=ROWS, row_count=2))
    for bare in ("\ntd {", "\nth {", "\ntable {", "\nth, td {"):
        assert bare not in html
    assert ".panel td {" in html


def test_the_tooltip_follows_the_charts_theme() -> None:
    """The tooltip is styled by vega-tooltip, not by this page's CSS, so its
    own `theme` option is the only thing that keeps it on the same surface as
    the chart it belongs to."""
    html = render_html(_spec(), ChartData(rows=ROWS, row_count=2))
    assert "tooltip: { theme: media.matches ? 'dark' : 'light' }" in html


def test_an_unplottable_spec_raises_rather_than_rendering_silently() -> None:
    """`render_html` must not swallow the error into a 200 embed.

    The Hub UI treats any rendered result as visible to the model, so a
    caught-and-printed error here would be invisible — see
    `test_an_unplottable_spec_returns_an_error` for the route-level contract
    this protects.
    """
    spec = DashboardSpec.model_validate(
        {
            **SPEC,
            "vega_lite": {
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
    """The property behind "the model supplies a `vega_lite` spec, never data values".

    It is structural rather than a matter of discipline: a spec is a query plus
    a `vega_lite` spec, and there is no field a caller can use to smuggle numbers
    past Cube — not as a `rows` key (rejected by `extra="forbid"`) and not as a
    `data` key inside `vega_lite` (rejected by the `VegaLiteSpec` validator). It is also
    what keeps a saved chart from going stale — there is no cached answer in the
    file to go stale.
    """
    with pytest.raises(ValidationError):
        DashboardSpec.model_validate({**SPEC, "rows": [{"ed_visits.avg_percent": 99}]})


# --- the embed contract ----------------------------------------------------


def test_render_requires_a_credential(client: TestClient) -> None:
    assert client.post("/tools/render_dashboard", json=SPEC).status_code == 401


def test_the_spec_advertises_html_for_the_embed(client: TestClient) -> None:
    """The Hub UI needs `text/html` on the response to treat it as an embed."""
    spec = client.get("/tools/openapi.json").json()
    content = spec["paths"]["/render_dashboard"]["post"]["responses"]["200"]["content"]
    assert "text/html" in content


def test_a_successful_render_returns_an_embeddable_response(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The embed contract, asserted rather than eyeballed.

    The chat surface only turns an external tool's result into an iframe when
    both headers are right. Lose either and the dashboard degrades into several
    kilobytes of markup pasted into the conversation — which renders as text, so
    nothing raises and nothing fails.
    """

    async def fake_query(self: object, query: object) -> dict[str, Any]:
        return {"rows": ROWS, "row_count": len(ROWS), "truncated": False, "applied_limit": 1000}

    monkeypatch.setattr("ohdp_agent.cube.CubeClient.run_metric_query", fake_query)

    response = authenticated_client.post("/tools/render_dashboard", json=SPEC)

    assert response.status_code == 200
    assert response.headers["content-disposition"] == "inline"
    assert response.headers["content-type"].startswith("text/html")
    assert "vegaEmbed" in response.text


def test_a_rejected_query_returns_cubes_own_reason(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cube names the wrong member; the model can fix its own spec from that."""

    async def fail(self: object, query: object) -> dict[str, Any]:
        raise CubeError("Member 'ed_visits.nope' not found")

    monkeypatch.setattr("ohdp_agent.cube.CubeClient.run_metric_query", fail)

    response = authenticated_client.post("/tools/render_dashboard", json=SPEC)
    assert response.status_code == 400
    assert "ed_visits.nope" in response.json()["detail"]


def test_an_unplottable_spec_returns_an_error_not_an_embed(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A spec/column mismatch must fail the tool call, not render a 200 embed.

    The Hub UI treats any rendered result as visible to the model — so a 200
    here, even with an in-page error paragraph, would look like success to the
    model and it would never retry with a fixed spec.
    """
    bad_spec = {
        **SPEC,
        "vega_lite": {
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

    response = authenticated_client.post("/tools/render_dashboard", json=bad_spec)
    assert response.status_code == 422
    assert "ed_visits.avg_percent" in response.json()["detail"]
    assert response.headers.get("content-disposition") != "inline"


# --- escaping, and the entity-copy-paste failure mode ----------------------
#
# The embed used to travel through Open WebUI, which HTML-entity-decoded the
# JSON string carrying it before parsing, so `_esc` escaped `&`/`<`/`>` twice
# to leave exactly one level after that decode. Open WebUI is gone (ADR-0017);
# the Hub UI sets this document straight as an iframe's `srcdoc`/`src` with no
# decode step, so a double escape now just leaves a literal `&gt;` sitting in
# the rendered card — including its own "Dashboard source" panel, which is
# exactly the shape a reader can select and paste back into a new spec. These
# tests cover both the escaping itself and the guardrail that catches a spec
# built from such a paste before it ever renders.


def test_the_embed_has_no_quote_entities() -> None:
    """`&quot;` in the embedded Cube query or YAML source is still avoided —
    `_esc` leaves quotes raw because every call site is text content, never an
    HTML attribute — even though the Open WebUI decode step that originally
    forced this choice is gone.
    """
    spec = DashboardSpec.model_validate(SPEC)
    document = render_html(spec, ChartData(rows=ROWS, row_count=2))

    assert "&quot;" not in document


def test_markup_in_a_title_cannot_survive_as_markup() -> None:
    """Titles are model-supplied and cell values come from the warehouse, so a
    `<script>` typed into either must come out as inert text, not live markup.
    """
    spec = DashboardSpec.model_validate({**SPEC, "title": "<script>alert(1)</script>"})
    document = render_html(spec, ChartData(rows=ROWS, row_count=2))

    assert "<script>alert(1)</script>" not in document
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in document


def test_an_entity_is_escaped_exactly_once() -> None:
    """The regression this file exists to prevent: a value that already
    contains a real `>` must render as one `&gt;`, not two escaped levels
    (`&amp;gt;`) and not zero (a raw `>` sitting in text content is harmless,
    but a doubled escape is the bug that shipped)."""
    spec = DashboardSpec.model_validate({**SPEC, "caption": "coverage >= 6 months"})
    document = render_html(spec, ChartData(rows=ROWS, row_count=2))

    assert "coverage &gt;= 6 months" in document
    assert "&amp;gt;" not in document


def test_a_literal_html_entity_in_a_filter_is_rejected_at_validation() -> None:
    """The actual incident: a `transform.filter` comparing against `'&gt;=6
    Months'` compiles as valid Vega-Lite — it's just a string literal — and
    matches no row that says `>=6 Months`, so the chart draws empty with no
    error anywhere. Caught here instead, with the offending text named.
    """
    bad_spec = {
        **SPEC,
        "vega_lite": {
            **SPEC["vega_lite"],
            "transform": [{"filter": "datum.stratification === '&gt;=6 Months'"}],
        },
    }
    with pytest.raises(ValidationError, match="&gt;"):
        DashboardSpec.model_validate(bad_spec)


def test_a_literal_html_entity_in_a_caption_is_rejected_at_validation() -> None:
    with pytest.raises(ValidationError, match="&amp;"):
        DashboardSpec.model_validate({**SPEC, "caption": "flu &amp; RSV coverage"})
