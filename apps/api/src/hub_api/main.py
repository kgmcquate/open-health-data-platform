"""FastAPI entrypoint.

Responsibilities (ARCHITECTURE.md §2, §5, §6):
  - OIDC session verification, `tier` claim extraction
  - entitlement + monthly quota checks BEFORE the chat agent is invoked
  - mint short-lived Cube service tokens (chat agent + dashboards)
  - Stripe webhook handling for tier changes
  - log every chat question/plan/result to Postgres (eval set)

The chat agent itself gets only two MCP connections (Cube, OpenMetadata) and no
raw SQL tool — ever.

hub-api used to serve the chat page itself at `/` as a shortcut to ship chat
without a second image. That page is gone now that `apps/web` (the Vite/React
hub) is the real UI — the chat there calls the same `/api/chat` route, and
this service is API-only.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from hub_api import db
from hub_api.auth import router as auth_router
from hub_api.chat import router as chat_router
from hub_api.content import router as content_router
from hub_api.content import seed_if_empty
from hub_api.dashboards import dashboards_router
from hub_api.issues import tools_app
from ohdp_shared import configure_logging, get_logger, settings

configure_logging(json=settings.log_json, level=settings.log_level)
log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
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
            seed_if_empty(engine)
            app.state.engine = engine
        except Exception as exc:  # noqa: BLE001 — see docstring
            log.error("database_unavailable", error=str(exc))
    else:
        log.warning("database_not_configured")
    yield


app = FastAPI(title="Open Health Data Platform — Hub API", version="0.0.0", lifespan=lifespan)

# Signed, HttpOnly session cookie carrying {email, name, tier} after OIDC
# login (hub_api.auth). This *is* the auth wall for the hub — no oauth2-proxy.
_secret = settings.session_secret_key
if not _secret:
    if settings.environment == "local":
        _secret = "dev-only-insecure-secret"
    else:
        log.error("session_secret_key_not_set")
        raise RuntimeError("OHDP_SESSION_SECRET_KEY must be set outside local dev.")
app.add_middleware(
    SessionMiddleware,
    secret_key=_secret,
    https_only=settings.environment != "local",
    same_site="lax",
    max_age=60 * 60 * 24 * 14,
)

app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(content_router)

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


# Routers to be added per milestone:
#   M2: /auth, /dashboards
#   M4: /billing/webhook, /tickets
