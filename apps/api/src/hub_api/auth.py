"""Built-in OIDC sign-in for the hub — replaces the oauth2-proxy wall.

ARCHITECTURE.md §5 assumed a hosted IdP plus an oauth2-proxy in front of every
surface. That is the right posture for third-party UIs we cannot modify
(Dagster, OpenMetadata), but it is a poor fit for our own app: the proxy cannot
render a landing page, cannot offer "browse without signing in", and puts a
second container and cookie domain between the user and the API. So the hub
runs the Authorization Code flow itself (Authlib), keeps the result in a
signed, HttpOnly session cookie (Starlette's SessionMiddleware), and resolves
identity from that cookie — never from a request header.

What a session carries: `{"user": {"email", "name", "tier"}}`. `tier` comes
from the `users` table at login time, so a Stripe-driven tier change takes
effect at next login; that is acceptable until billing ships (M4).

The **admin role is deliberately not in that dict**. It is derived from
`settings.admin_emails_list` on every read (`is_admin`), so a session cookie
issued before someone was made an admin — or after they stopped being one —
carries no stale authority. `require_admin` is the dependency that gates the
destructive routes; today that is deleting a published dashboard
(`hub_api.content`).

The `/tools` sub-app (`hub_api.issues.get_reporter`) reads the same session
cookie for its browser callers, and falls back to a shared bearer token for
the configured `ohdp-tools` MCP/OpenAPI connection, which has no session of its
own to carry.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import Column, DateTime, Integer, String, Table, select
from sqlalchemy.engine import Engine

from hub_api import db
from ohdp_shared import get_logger, settings

log = get_logger(__name__)

# Lives on db.metadata so `db.ensure_schema` creates it with the chat log.
users = Table(
    "users",
    db.metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    # The IdP-verified email. Unique, because tier is attached to it.
    Column("email", String(320), nullable=False, unique=True, index=True),
    Column("name", String(320), nullable=False, default=""),
    # "free" | "paid". Bumped by the billing webhook (M4); "free" until then.
    Column("tier", String(16), nullable=False, default="free"),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("last_login_at", DateTime(timezone=True), nullable=False),
)

oauth = OAuth()
if settings.oidc_client_id:
    oauth.register(
        name="oidc",
        client_id=settings.oidc_client_id,
        client_secret=settings.oidc_client_secret,
        server_metadata_url=(
            f"{settings.oidc_issuer.rstrip('/')}/.well-known/openid-configuration"
        ),
        client_kwargs={"scope": "openid email profile"},
    )


class User(BaseModel):
    """The signed-in user, as the rest of the hub sees them."""

    email: str
    name: str = ""
    tier: str = "free"
    # Derived from `settings.admin_emails_list` on every read — never taken
    # from the session cookie, even if an older cookie carries the field. See
    # `is_admin`.
    is_admin: bool = False


def is_admin(email: str) -> bool:
    """Whether this verified email holds the admin role.

    Read from configuration at call time rather than from the session or the
    `users` table, and that is the whole design:

      - **Not the session.** A session cookie lives fourteen days, so a role
        baked into one at login outlives the decision to revoke it by up to
        that long. `tier` accepts that lag because it only widens a quota;
        admin permits destroying other people's published work, so it is
        resolved per request and a removed admin loses the role on the next
        one.
      - **Not the database.** `users` is written by the login path. Keeping
        the role out of it means no code path that touches that table can
        grant admin, and the set of admins is reviewable in the deployment's
        values file rather than only in production data.

    The cost is that adding an admin is a deploy (`OHDP_ADMIN_EMAILS`), not a
    click. At one operator that is the right trade; a hub with real admin
    turnover would want a `role` column plus an admin-only route to set it,
    and this function is the seam to change.
    """
    return email.lower() in settings.admin_emails_list


def get_current_user(request: Request) -> User:
    """Identity from the signed session cookie. No session, no access."""
    data = request.session.get("user")
    if not data:
        raise HTTPException(401, "Not signed in.")
    email = str(data.get("email", ""))
    return User(**{**data, "email": email, "is_admin": is_admin(email)})


def get_user_tier(request: Request) -> str:
    """This request's tier, without building the rest of `User` — `chat.py`'s
    quota gates and Cube calls want only this field, on the hot path for
    every question, the same way `chat.get_user_email` is a lighter sibling
    of `get_current_user` for routes that only need the address.

    Same lag as this module's docstring describes for `tier` generally: a
    Stripe-driven upgrade takes effect at the session's next login, not the
    moment the webhook lands. "free" for a request with no session at all —
    every caller of this is already behind its own `get_user_email` check,
    so that default is a defensive fallback, not a real code path.
    """
    user = request.session.get("user")
    return str(user.get("tier", "free")) if user else "free"


def require_admin(user: Annotated[User, Depends(get_current_user)]) -> User:
    """An admin, or 403. The wall in front of every destructive route.

    403 rather than 404: the caller is signed in and the resource they named
    is real, so hiding its existence buys nothing and costs a confusing error.
    """
    if not user.is_admin:
        log.warning("admin_route_refused", email=user.email)
        raise HTTPException(403, "Admin access is required.")
    return user


def _upsert_user(engine: Engine, *, email: str, name: str) -> str:
    """Record the login and return the user's current tier."""
    now = datetime.now(UTC)
    with engine.begin() as connection:
        row = connection.execute(select(users.c.tier).where(users.c.email == email)).first()
        if row is None:
            connection.execute(
                users.insert().values(
                    email=email,
                    name=name,
                    tier="free",
                    created_at=now,
                    last_login_at=now,
                )
            )
            return "free"
        connection.execute(
            users.update().where(users.c.email == email).values(name=name, last_login_at=now)
        )
        return str(row.tier)


router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/login")
async def login(request: Request) -> RedirectResponse:
    if not settings.oidc_client_id:
        if settings.environment != "local":
            raise HTTPException(503, "Sign-in is not configured on this deployment.")
        # No Google client needed to develop locally: skip the OAuth round trip
        # and drop straight into a signed-in session, the same way main.py
        # falls back to an insecure session secret rather than requiring one
        # outside local dev.
        request.session["user"] = {"email": "dev@localhost", "name": "Local Dev", "tier": "free"}
        log.warning("local_auth_bypass", email="dev@localhost")
        return RedirectResponse("/", status_code=303)
    return await oauth.oidc.authorize_redirect(  # type: ignore[no-any-return]
        request, f"{settings.hub_base_url.rstrip('/')}/auth/callback"
    )


@router.get("/callback")
async def callback(request: Request) -> RedirectResponse:
    token = await oauth.oidc.authorize_access_token(request)
    info = token.get("userinfo") or {}
    email = info.get("email")
    if not email:
        # An IdP that will not verify an email is not one we can quota against.
        raise HTTPException(400, "The identity provider did not return an email.")
    allowed = settings.allowed_emails_list
    if allowed and email.lower() not in allowed:
        # The access control oauth2-proxy-app.yaml's `authenticatedEmailsFile`
        # used to provide, before the hub did its own OIDC. Model API spend is
        # metered per verified email (chat.py), so this is what stands between
        # that quota and a public sign-up page until billing (M4) exists.
        log.warning("login_rejected_not_allowlisted", email=email)
        raise HTTPException(403, "This deployment is not open to new sign-ins yet.")
    tier = "free"
    engine: Engine | None = getattr(request.app.state, "engine", None)
    if engine is not None:
        tier = _upsert_user(engine, email=email, name=info.get("name", ""))
    else:
        # Signing in without the user store means quotas cannot be enforced;
        # allow the session so the rest of the site works, but chat will 503
        # at its own engine check, which is the honest failure mode.
        log.error("login_without_database", email=email)
    request.session["user"] = {"email": email, "name": info.get("name", ""), "tier": tier}
    log.info("login", email=email, tier=tier)
    return RedirectResponse("/", status_code=303)


@router.post("/logout")
def logout(request: Request) -> dict[str, bool]:
    request.session.clear()
    return {"ok": True}


@router.get("/me")
def auth_me(user: Annotated[User, Depends(get_current_user)]) -> User:
    return user
