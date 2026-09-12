"""The chat agent's tool layer (ARCHITECTURE.md §6, ADR-0016, docs/chatbot.md).

Three tool groups, none of which can express a raw SQL query:

  - `cube`       — execution. Measures and dimensions over Cube Core's REST API.
  - `literature` — Europe PMC search, for questions that start outside the warehouse.
  - `catalog`    — context and discovery over OpenMetadata's MCP server (M3.2).

Lives inside apps/api rather than as a fourth standalone uv project: hub-api is
its only consumer, and this way it inherits that project's CI matrix entry,
mypy-strict setting, and container image with no new plumbing. It is still an
independent import (`ohdp_agent`, not `hub_api.agent`) so nothing in here may
depend on FastAPI or on request state.
"""

from ohdp_agent.models import CubeQuery, Filter, TimeDimension

__all__ = ["CubeQuery", "Filter", "TimeDimension"]
