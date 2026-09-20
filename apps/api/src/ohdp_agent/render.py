"""How a `CubeInfo` is described to a model.

Used by the in-process tool-use loop (`ohdp_agent.loop`) to put Cube's metadata
in front of an LLM. Members are always fully qualified — `cube_name.field_name`
— because that is the only form `models.CubeQuery` accepts.

Cube's `/v1/meta` already returns member names qualified (`air_quality.avg_value`,
not `avg_value`), so `_qualify` prefixes only when the name is bare. Prefixing
unconditionally produced `air_quality.air_quality.avg_value`, which has two dots
and is therefore rejected by `models.MEMBER_RE` — the model was being handed
member names it could not then use in a query.
"""

from __future__ import annotations

from typing import Any

from ohdp_agent.cube import CubeInfo


def _qualify(cube_name: str, member_name: str) -> str:
    """`cube_name.field_name`, whether or not Cube already qualified it."""
    if member_name.startswith(f"{cube_name}."):
        return member_name
    return f"{cube_name}.{member_name}"


def measures_json(cube: CubeInfo, *, with_agg: bool = False) -> list[dict[str, Any]]:
    return [
        {
            "name": _qualify(cube.name, m.name),
            **({"agg": m.agg_type} if with_agg else {}),
            "description": m.description,
        }
        for m in cube.measures
    ]


def dimensions_json(cube: CubeInfo) -> list[dict[str, Any]]:
    return [
        {"name": _qualify(cube.name, d.name), "type": d.type, "description": d.description}
        for d in cube.dimensions
    ]


def cube_json(cube: CubeInfo, *, with_agg: bool = False) -> dict[str, Any]:
    """One cube in full — what `describe_metric` returns."""
    return {
        "cube": cube.name,
        "description": cube.description,
        "measures": measures_json(cube, with_agg=with_agg),
        "dimensions": dimensions_json(cube),
    }


def catalog_json(cubes: tuple[CubeInfo, ...], *, with_agg: bool = False) -> list[dict[str, Any]]:
    """Every cube — what `list_metrics` returns. The agent's whole world."""
    return [cube_json(c, with_agg=with_agg) for c in cubes]
