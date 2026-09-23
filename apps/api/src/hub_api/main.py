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
hub) is the real UI — the chat there calls the same `/api/chat` route. Rather
than run a second image for it, the Dockerfile builds `apps/web` and this
service serves the result as static files (below), so there is still one
image and one Deployment.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from hub_api import db
from hub_api.auth import router as auth_router
from hub_api.chat import router as chat_router
from hub_api.content import ensure_columns_and_indexes
from hub_api.content import router as content_router
from hub_api.dashboards import dashboards_router
from hub_api.issues import tools_app
from hub_api.models import build_agents, discover_openai_models
from hub_api.tool_connections import build_local_openapi_toolset, load_tool_connections
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
    # Every `ask_user` question currently blocking a chat run, keyed by ask_id
    # (hub_api.chat.get_pending_asks). Process-local: the POST that answers one
    # must reach the event loop still awaiting it, which one replica makes true
    # by construction.
    app.state.pending_asks = {}
    # The same pool, on the mounted sub-app as well: a mounted app resets
    # `scope["app"]` to itself, so `/tools` routes reading `request.app.state`
    # (hub_api.dashboards.get_library_engine, for the dashboard library) see
    # `tools_app.state`, not this one. Set to None first for the same reason
    # `app.state.engine` is: the attribute must exist even when Postgres does
    # not, so those routes answer 503 instead of AttributeError-ing into a 500.
    tools_app.state.engine = None
    if settings.app_database_url:
        try:
            engine = db.make_engine(settings.app_database_url)
            db.ensure_schema(engine)
            ensure_columns_and_indexes(engine)
            app.state.engine = engine
            tools_app.state.engine = engine
        except Exception as exc:  # noqa: BLE001 — see docstring
            log.error("database_unavailable", error=str(exc))
    else:
        log.warning("database_not_configured")

    openai_models = await discover_openai_models() if settings.openai_backends else {}
    tool_connections = await load_tool_connections()
    # `tools_app` (render_dashboard, save_dashboard, get_dashboard,
    # report_issue) is this same process's own
    # `/tools` app, already fully assembled by the module-level code below —
    # wired in-process rather than as a `tools.yaml` connection so building it
    # never depends on hub-api being up enough to answer its own request (see
    # `build_local_openapi_toolset`'s docstring).
    tool_connections["ohdp-tools"] = build_local_openapi_toolset(
        app,
        tools_app,
        id="ohdp-tools",
        mount_path="/tools",
        headers={"Authorization": f"Bearer {settings.tools_auth_token}"},
    )
    app.state.agents = build_agents(openai_models, tool_connections)
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
# its own routes. That narrow spec is what the chat model's "ohdp-tools"
# toolset is built from below (`build_local_openapi_toolset`, in `lifespan`),
# and it is the reason the chat model cannot see `/api/chat` as a callable
# tool (hub_api.issues). The hub's own page calls the same route, reading the
# same session cookie (mounted apps share the parent's SessionMiddleware
# scope), so there is one implementation behind both callers.
# The dashboard tools join the same sub-app rather than getting one of their
# own, because the tool connection reads one spec per registered app and every
# operation in it becomes a tool. Keeping them together means one app, one
# bearer token and one place to check what the chat model can actually call.
tools_app.include_router(dashboards_router)

app.mount("/tools", tools_app)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "environment": settings.environment}


# The Dockerfile builds `apps/web` and copies its dist/ here. Locally the
# directory doesn't exist — `apps/web` runs under `vite dev` instead, proxying
# /api and /auth to this service (apps/web/vite.config.ts) — so this is a
# production-only mount, not a local-dev branch like the ones above.
_web_dist = Path("/app/web/dist")
if _web_dist.is_dir():
    app.mount("/assets", StaticFiles(directory=_web_dist / "assets"), name="web-assets")

    @app.get("/favicon.svg")
    async def favicon() -> FileResponse:
        """Vite copies apps/web/public/* to dist/ root, not dist/assets/, so
        it isn't covered by the /assets mount above and would otherwise fall
        through to the catch-all below and get index.html instead."""
        return FileResponse(_web_dist / "favicon.svg")

    @app.get("/{full_path:path}")
    async def web_app(full_path: str) -> FileResponse:
        """Every GET not claimed by a router above falls through to the SPA
        shell, so react-router can handle deep links (e.g. a reload on
        /chat) without a matching server-side route.

        Except under the API prefixes: a typo'd or removed endpoint there
        should 404, not silently return the HTML shell with a 200."""
        if full_path.startswith(("api/", "auth/", "tools/")):
            raise HTTPException(404)
        return FileResponse(_web_dist / "index.html")


# Routers to be added per milestone:
#   M2: /auth, /dashboards
#   M4: /billing/webhook, /tickets
