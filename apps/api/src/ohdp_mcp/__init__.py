"""Cube Core's tool surface, served over MCP (ADR-0017).

`ohdp_agent.cube` is the implementation; this package is only a transport. The
safety properties ADR-0016 relies on — no SQL tool, `CubeQuery` validation, the
row cap — are unchanged, because they live in `ohdp_agent.models` and are
enforced on this side of the wire regardless of who is calling.
"""

from ohdp_mcp.server import create_app, mcp

__all__ = ["create_app", "mcp"]
