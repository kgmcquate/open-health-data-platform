"""The Cube query model is the primary safety control (ARCHITECTURE.md §1.6).

These tests are mostly about what the model *refuses*. If one of them starts
failing, the agent's reachable surface just got wider than it was designed to be.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ohdp_agent.models import MAX_ROWS, CubeQuery, Filter, TimeDimension


def test_accepts_a_plain_query() -> None:
    query = CubeQuery(
        measures=["air_quality.avg_value"],
        dimensions=["air_quality.country"],
        limit=500,
    )
    assert query.to_cube_json() == {
        "limit": 500,
        "measures": ["air_quality.avg_value"],
        "dimensions": ["air_quality.country"],
    }


@pytest.mark.parametrize(
    "member",
    [
        "air_quality",  # no field
        "air_quality.avg_value; DROP TABLE users",
        "air_quality.avg_value UNION SELECT 1",
        "(SELECT 1).x",
        "air_quality.*",
        "AirQuality.avgValue",  # Cube identifiers are snake_case
        "air quality.avg",
        "'; --",
        "a" * 200 + ".b",
    ],
)
def test_rejects_anything_that_is_not_a_member(member: str) -> None:
    """No character sequence in a member may reach Cube as SQL."""
    with pytest.raises(ValidationError):
        CubeQuery(measures=[member])


def test_rejects_unknown_fields() -> None:
    """`extra="forbid"` is an allowlist we never have to maintain as Cube grows."""
    with pytest.raises(ValidationError):
        CubeQuery(measures=["air_quality.avg_value"], ungrouped=True)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        CubeQuery(measures=["air_quality.avg_value"], sql="SELECT 1")  # type: ignore[call-arg]


def test_rejects_an_empty_selection() -> None:
    with pytest.raises(ValidationError):
        CubeQuery()


def test_limit_is_capped_at_max_rows() -> None:
    with pytest.raises(ValidationError):
        CubeQuery(measures=["air_quality.avg_value"], limit=MAX_ROWS + 1)


def test_rejects_unknown_operators() -> None:
    with pytest.raises(ValidationError):
        Filter(member="air_quality.country", operator="regex", values=["x"])  # type: ignore[arg-type]


def test_valueless_operators_take_no_values() -> None:
    assert Filter(member="air_quality.country", operator="set").values == []
    with pytest.raises(ValidationError):
        Filter(member="air_quality.country", operator="set", values=["US"])
    with pytest.raises(ValidationError):
        Filter(member="air_quality.country", operator="equals", values=[])


def test_time_dimension_round_trips() -> None:
    query = CubeQuery(
        measures=["air_quality.avg_value"],
        time_dimensions=[
            TimeDimension(
                dimension="air_quality.measurement_date",
                granularity="day",
                date_range=["2024-01-01", "2024-12-31"],
            )
        ],
        filters=[Filter(member="air_quality.parameter", operator="equals", values=["pm25"])],
        order={"air_quality.measurement_date": "asc"},
    )
    body = query.to_cube_json()
    assert body["timeDimensions"] == [
        {
            "dimension": "air_quality.measurement_date",
            "granularity": "day",
            "dateRange": ["2024-01-01", "2024-12-31"],
        }
    ]
    assert body["filters"] == [
        {"member": "air_quality.parameter", "operator": "equals", "values": ["pm25"]}
    ]
    assert body["order"] == {"air_quality.measurement_date": "asc"}


def test_a_list_date_range_must_be_a_pair() -> None:
    with pytest.raises(ValidationError):
        TimeDimension(dimension="air_quality.measurement_date", date_range=["2024-01-01"])


def test_valueless_filter_omits_values_on_the_wire() -> None:
    """Cube rejects `set` filters that carry a `values` key at all."""
    query = CubeQuery(
        measures=["air_quality.avg_value"],
        filters=[Filter(member="air_quality.country", operator="set")],
    )
    assert query.to_cube_json()["filters"] == [{"member": "air_quality.country", "operator": "set"}]
