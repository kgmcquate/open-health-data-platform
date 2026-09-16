"""FastAPI entrypoint.

Responsibilities (ARCHITECTURE.md §2, §5, §6):
  - OIDC session verification, `tier` claim extraction
  - entitlement + monthly quota checks BEFORE the chat agent is invoked
  - mint short-lived Cube service tokens (chat agent + dashboards)
  - Stripe webhook handling for tier changes
  - log every chat question/plan/result to Postgres (eval set)

The chat agent itself gets only two MCP connections (Cube, OpenMetadata) and no
raw SQL tool — ever.

hub-api also serves the chat page itself at `/`. ARCHITECTURE.md §4 lists a
separate `hub-web` container for this, and docs/chatbot.md §5 puts the chat UI
there; one static page served from here is a deliberate shortcut, taken so the
chat could ship without a second image, a second chart, and an ingress split.
It stays the right call while the UI is one page with no build step. It stops
being the right call the moment the hub grows a landing page, billing screens,
and embedded dashboards — at which point `apps/web` gets scaffolded for real and
this router goes away.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from hub_api import db
from hub_api.chat import router as chat_router
from hub_api.dashboards import dashboards_router
from hub_api.issues import tools_app
from ohdp_shared import configure_logging, get_logger, settings

configure_logging(json=settings.log_json, level=settings.log_level)
log = get_logger(__name__)

STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open the pool and create the chat-log table.

    A database that is not there must not stop `/healthz` from answering — the
    liveness probe would then kill a pod that is only waiting for Postgres to
    finish restarting. `/api/chat` returns 503 while `state.engine` is unset.
    """
    app.state.engine = None
    if settings.app_database_url:
        try:
            engine = db.make_engine(settings.app_database_url)
            db.ensure_schema(engine)
            app.state.engine = engine
        except Exception as exc:  # noqa: BLE001 — see docstring
            log.error("database_unavailable", error=str(exc))
    else:
        log.warning("database_not_configured")
    yield


app = FastAPI(title="Open Health Data Platform — Hub API", version="0.0.0", lifespan=lifespan)
app.include_router(chat_router)

# Mounted, not included: a sub-app carries its own `/openapi.json`, listing only
# its own routes. That narrow spec is what Open WebUI is pointed at, and it is
# the reason the chat model cannot see `/api/chat` as a callable tool
# (hub_api.issues). The hub's own page calls the same route through the same
# oauth2-proxy wall, so there is one implementation behind both surfaces.
# The dashboard tools join the same sub-app rather than getting one of their
# own, because Open WebUI reads one spec per registered tool server and every
# operation in it becomes a tool. Keeping them together means one URL, one
# bearer token and one place to check what the chat model can actually call.
tools_app.include_router(dashboards_router)

app.mount("/tools", tools_app)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "environment": settings.environment}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


# Routers to be added per milestone:
#   M2: /auth, /dashboards (link out to Streamlit — see ADR-0015)
#   M4: /billing/webhook, /tickets
