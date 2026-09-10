"""FastAPI entrypoint.

Responsibilities (ARCHITECTURE.md §2, §5, §6):
  - OIDC session verification, `tier` claim extraction
  - entitlement + monthly quota checks BEFORE the chat agent is invoked
  - mint short-lived Cube service tokens and Superset guest tokens
  - Stripe webhook handling for tier changes
  - log every chat question/plan/result to Postgres (eval set)

The chat agent itself gets only two MCP connections (Cube, OpenMetadata) and no
raw SQL tool — ever.
"""

from __future__ import annotations

from fastapi import FastAPI

from ohdp_shared import configure_logging, settings

configure_logging(json=settings.log_json, level=settings.log_level)

app = FastAPI(title="Open Health Data Platform — Hub API", version="0.0.0")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "environment": settings.environment}


# Routers to be added per milestone:
#   M2: /auth, /dashboards (guest token mint)
#   M3: /chat  (quota gate -> agent -> logged result)
#   M4: /billing/webhook, /tickets
