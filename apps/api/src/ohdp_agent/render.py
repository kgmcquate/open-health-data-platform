"""How a `CubeInfo` is described to a model.

Shared by the two callers that put Cube's metadata in front of an LLM — the
in-process tool-use loop (`ohdp_agent.loop`) and the MCP server
(`ohdp_mcp.server`). It lives here rather than in either of them because the
two must agree: ADR-0016 makes the OM-Metric ↔ Cube-measure mapping
load-bearing, and a member name rendered one way in the chat agent and another
way over MCP is exactly the kind of drift that fails quietly.

Members are always fully qualified — `cube_name.field_name` — because that is
the only form `models.CubeQuery` accepts.

Cube's `/v1/meta` already returns member names qualified (`air_quality.avg_value`,
not `avg_value`), so `_qualify` prefixes only when the name is bare. Prefixing
unconditionally produced `air_quality.air_quality.avg_value`, which has two dots
and is therefore rejected by `models.MEMBER_RE` — the model was being handed
member names it could not then use in a query.
"""

from __future__ import annotations

from typing import Any

from ohdp_agent.cube import CubeInfo, DimensionInfo, MeasureInfo


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


def index_json(cubes: tuple[CubeInfo, ...]) -> list[dict[str, Any]]:
    """What `list_metrics` returns: each cube's description and member *names*.

    Member descriptions and types are most of `/meta`'s bulk, and a model
    scanning for a candidate cube does not need them — `describe_metric` gives
    them for the one cube it settles on. Names stay fully qualified so a query
    can still be written from this alone.
    """
    return [
        {
            "cube": c.name,
            "description": c.description,
            "measures": [_qualify(c.name, m.name) for m in c.measures],
            "dimensions": [_qualify(c.name, d.name) for d in c.dimensions],
        }
        for c in cubes
    ]


def search_cubes(cubes: tuple[CubeInfo, ...], search: str | None) -> tuple[CubeInfo, ...]:
    """Cubes matching any whitespace-separated term, best match first.

    A term matches as a case-insensitive substring of the cube's name, title or
    description, or of any member's name, title or description. No `search`
    (or a blank one) means every cube.
    """
    terms = [t for t in (search or "").lower().split() if t]
    if not terms:
        return cubes

    def hits(cube: CubeInfo) -> int:
        members: list[MeasureInfo | DimensionInfo] = [*cube.measures, *cube.dimensions]
        haystack = " ".join(
            [cube.name, cube.title, cube.description]
            + [f"{m.name} {m.title} {m.description}" for m in members]
        ).lower()
        return sum(term in haystack for term in terms)

    scored = [(hits(c), i, c) for i, c in enumerate(cubes)]
    return tuple(c for n, _, c in sorted(scored, key=lambda s: (-s[0], s[1])) if n > 0)
