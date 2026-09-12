"""The Cube query object the agent is allowed to build.

This module is the primary safety control (ARCHITECTURE.md §1.6, §6). The agent
never emits SQL; it fills in this model, and anything that does not fit is
rejected before a request leaves hub-api. Two properties do the work:

  - **`extra="forbid"` everywhere.** Cube's query language has fields we do not
    want the agent reaching for (`ungrouped`, `subqueryJoins`, raw `sql`...).
    An allowlist of fields is a denylist we never have to maintain.
  - **Members are pattern-matched, not string-concatenated.** A member is
    `cube_name.field_name` and nothing else, so there is no character sequence
    the agent can put here that Cube will parse as SQL.

Caps are enforced in three independent places (docs/chatbot.md §6). This is the
first: defensive, and deliberately stricter than Cube's own `queryRewrite`,
which remains authoritative.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Hard ceiling applied here as well as in semantic/cube/cube.js. If these two
# ever disagree, cube.js wins — it is the one an attacker cannot skip.
MAX_ROWS = 10_000

# `cube_name.field_name`. Cube identifiers are snake_case; nothing else parses.
MEMBER_RE = re.compile(r"^[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*$")

Member = Annotated[str, Field(pattern=MEMBER_RE.pattern, max_length=128)]

Granularity = Literal["second", "minute", "hour", "day", "week", "month", "quarter", "year"]

# Cube's filter operators, enumerated. Anything outside this set is rejected
# rather than forwarded — an unknown operator is either a model mistake or an
# attempt at something we have not reasoned about.
Operator = Literal[
    "equals",
    "notEquals",
    "contains",
    "notContains",
    "startsWith",
    "endsWith",
    "gt",
    "gte",
    "lt",
    "lte",
    "set",
    "notSet",
    "inDateRange",
    "notInDateRange",
    "beforeDate",
    "afterDate",
]

# Operators that take no `values` — Cube rejects the query if you send them any.
_VALUELESS_OPERATORS = frozenset({"set", "notSet"})


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Filter(_Strict):
    """A single flat predicate.

    Cube supports nested boolean groups (`{"and": [...]}`). They are deliberately
    not modelled: flat AND-ed filters answer every question in the seed eval set,
    and arbitrary nesting is a much larger surface to reason about. Revisit only
    with a question that genuinely needs it.
    """

    member: Member
    operator: Operator
    values: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("values")
    @classmethod
    def _values_match_operator(cls, v: list[str], info: object) -> list[str]:
        for value in v:
            if len(value) > 512:
                raise ValueError("filter value too long")
        return v

    def model_post_init(self, _context: object) -> None:
        if self.operator in _VALUELESS_OPERATORS:
            if self.values:
                raise ValueError(f"operator {self.operator!r} takes no values")
        elif not self.values:
            raise ValueError(f"operator {self.operator!r} requires at least one value")


class TimeDimension(_Strict):
    """A time dimension with an optional granularity and date range.

    `date_range` accepts either Cube's relative strings ("last 30 days") or a
    two-element [start, end] list of ISO dates — both are Cube-side constructs,
    not SQL.
    """

    dimension: Member
    granularity: Granularity | None = None
    date_range: str | list[str] | None = None

    @field_validator("date_range")
    @classmethod
    def _check_range(cls, v: str | list[str] | None) -> str | list[str] | None:
        if isinstance(v, list) and len(v) != 2:
            raise ValueError("a list date_range must be exactly [start, end]")
        return v


class CubeQuery(_Strict):
    """The complete set of things the agent may ask the warehouse for."""

    measures: list[Member] = Field(default_factory=list, max_length=10)
    dimensions: list[Member] = Field(default_factory=list, max_length=10)
    time_dimensions: list[TimeDimension] = Field(default_factory=list, max_length=3)
    filters: list[Filter] = Field(default_factory=list, max_length=20)
    order: dict[Member, Literal["asc", "desc"]] = Field(default_factory=dict)
    limit: int = Field(default=1_000, ge=1, le=MAX_ROWS)

    def model_post_init(self, _context: object) -> None:
        if not self.measures and not self.dimensions:
            raise ValueError("a query must select at least one measure or dimension")

    def to_cube_json(self) -> dict[str, object]:
        """Render Cube's wire format (camelCase), omitting empty keys.

        Built by hand rather than with a serialization alias so the exact shape
        crossing the wire is visible in one place and testable.
        """
        query: dict[str, object] = {"limit": self.limit}
        if self.measures:
            query["measures"] = list(self.measures)
        if self.dimensions:
            query["dimensions"] = list(self.dimensions)
        if self.time_dimensions:
            query["timeDimensions"] = [
                {
                    k: v
                    for k, v in (
                        ("dimension", td.dimension),
                        ("granularity", td.granularity),
                        ("dateRange", td.date_range),
                    )
                    if v is not None
                }
                for td in self.time_dimensions
            ]
        if self.filters:
            query["filters"] = [
                {
                    k: v
                    for k, v in (
                        ("member", f.member),
                        ("operator", f.operator),
                        ("values", list(f.values) if f.values else None),
                    )
                    if v is not None
                }
                for f in self.filters
            ]
        if self.order:
            query["order"] = dict(self.order)
        return query
