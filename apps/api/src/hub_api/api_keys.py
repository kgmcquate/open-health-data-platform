"""Personal API keys for the paid data API (`hub_api.gateway`, ADR-0030).

A Plus subscriber creates keys on the Developer page and presents one as
`Authorization: Bearer ohdp_...` to `/v1/...`: Cube's REST API, the Cube MCP
server and the catalog MCP server. The browser routes here (`/api/keys`)
manage keys with the ordinary session cookie. The gateway only ever sees a key.

Choices worth writing down:

  - **Only a hash is stored.** A key is 32 random bytes, so a plain SHA-256 is
    enough. A slow password hash buys nothing against a secret nobody can guess,
    and it would cost one on every API call. The plaintext is returned once,
    at creation, and never again.
  - **Tier is read from `users` on every call, not from the session.** The
    session cookie's `tier` lags a Stripe change until the next login
    (`hub_api.auth`), which is fine for widening a chat quota. For a paid API
    it would mean a cancelled subscriber keeps access for up to two weeks. So
    `resolve_key` joins `users` on each request, and a downgrade from the
    billing webhook cuts access at the next call.
  - **Creating a key needs Plus; holding one doesn't.** Keys survive a
    downgrade, so re-subscribing brings the same keys back to life. Every call
    is refused with 402 in between.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import Column, DateTime, Integer, String, Table, func, select
from sqlalchemy.engine import Engine

from hub_api import db
from hub_api.auth import User, get_current_user, users
from ohdp_shared import get_logger

log = get_logger(__name__)

KEY_PREFIX = "ohdp_"
MAX_ACTIVE_KEYS = 5
# `last_used_at` is for the Developer page ("used 3 minutes ago"), not an audit
# log, so it is written at most this often per key rather than on every call.
_LAST_USED_RESOLUTION = timedelta(minutes=1)

# Only the tier that pays for the API may use it. A set so a future tier that
# includes it is a one-word change.
API_TIERS = frozenset({"plus"})

api_keys = Table(
    "api_keys",
    db.metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_email", String(320), nullable=False, index=True),
    Column("name", String(100), nullable=False, default=""),
    # The first characters of the key, shown on the Developer page so a user
    # can tell their keys apart. Not secret: it identifies, it doesn't unlock.
    Column("prefix", String(16), nullable=False),
    Column("key_hash", String(64), nullable=False, unique=True, index=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("last_used_at", DateTime(timezone=True), nullable=True),
    Column("revoked_at", DateTime(timezone=True), nullable=True),
    sqlite_autoincrement=True,
)


@dataclass(frozen=True)
class ApiCaller:
    """Who a key belongs to, and their tier as of this request."""

    email: str
    tier: str
    key_id: int


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def create_key(engine: Engine, *, user_email: str, name: str) -> tuple[int, str]:
    """Mint a key for `user_email`. Returns `(id, plaintext)`; the plaintext
    exists only in this return value."""
    key = KEY_PREFIX + secrets.token_urlsafe(32)
    with engine.begin() as connection:
        result = connection.execute(
            api_keys.insert().values(
                user_email=user_email,
                name=name,
                prefix=key[: len(KEY_PREFIX) + 6],
                key_hash=_hash(key),
                created_at=datetime.now(UTC),
            )
        )
        inserted = result.inserted_primary_key
        assert inserted is not None
        key_id = int(inserted[0])
    log.info("api_key_created", user=user_email, key_id=key_id)
    return key_id, key


def active_key_count(engine: Engine, *, user_email: str) -> int:
    statement = (
        select(func.count())
        .select_from(api_keys)
        .where(api_keys.c.user_email == user_email)
        .where(api_keys.c.revoked_at.is_(None))
    )
    with engine.connect() as connection:
        return int(connection.execute(statement).scalar_one())


def list_keys(engine: Engine, *, user_email: str) -> list[dict[str, object]]:
    """The user's keys that aren't revoked, newest first. Never the hash."""
    statement = (
        select(
            api_keys.c.id,
            api_keys.c.name,
            api_keys.c.prefix,
            api_keys.c.created_at,
            api_keys.c.last_used_at,
        )
        .where(api_keys.c.user_email == user_email)
        .where(api_keys.c.revoked_at.is_(None))
        .order_by(api_keys.c.created_at.desc(), api_keys.c.id.desc())
    )
    with engine.connect() as connection:
        return [dict(row._mapping) for row in connection.execute(statement)]


def revoke_key(engine: Engine, *, user_email: str, key_id: int) -> bool:
    """Revoke one of `user_email`'s keys. False if they have no such active key,
    which covers someone else's key id too."""
    with engine.begin() as connection:
        result = connection.execute(
            api_keys.update()
            .where(api_keys.c.id == key_id)
            .where(api_keys.c.user_email == user_email)
            .where(api_keys.c.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
    if result.rowcount:
        log.info("api_key_revoked", user=user_email, key_id=key_id)
    return bool(result.rowcount)


def resolve_key(engine: Engine, key: str) -> ApiCaller | None:
    """The owner of an active key, with their tier read from `users` now.

    None for anything that isn't a live key: wrong shape, unknown, revoked, or
    a key whose owner has no `users` row.
    """
    if not key.startswith(KEY_PREFIX):
        return None
    statement = (
        select(api_keys.c.id, api_keys.c.user_email, api_keys.c.last_used_at, users.c.tier)
        .join(users, users.c.email == api_keys.c.user_email)
        .where(api_keys.c.key_hash == _hash(key))
        .where(api_keys.c.revoked_at.is_(None))
    )
    with engine.connect() as connection:
        row = connection.execute(statement).first()
    if row is None:
        return None
    _touch(engine, key_id=int(row.id), last_used_at=row.last_used_at)
    return ApiCaller(email=str(row.user_email), tier=str(row.tier), key_id=int(row.id))


def _touch(engine: Engine, *, key_id: int, last_used_at: datetime | None) -> None:
    now = datetime.now(UTC)
    if last_used_at is not None:
        # SQLite hands back a naive datetime for a timezone-aware column.
        if last_used_at.tzinfo is None:
            last_used_at = last_used_at.replace(tzinfo=UTC)
        if now - last_used_at < _LAST_USED_RESOLUTION:
            return
    with engine.begin() as connection:
        connection.execute(
            api_keys.update().where(api_keys.c.id == key_id).values(last_used_at=now)
        )


class ApiKeyError(Exception):
    """Why a request to `/v1` was refused before it did anything."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def authenticate(engine: Engine, authorization: str | None) -> ApiCaller:
    """The caller behind an `Authorization` header, or `ApiKeyError`.

    401 for no key or a dead one, 402 for a live key whose owner isn't on a
    tier that includes the API. Shared by the FastAPI dependency below and the
    plain-ASGI MCP endpoints in `hub_api.gateway`, so the two can't disagree.
    """
    scheme, _, presented = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not presented.strip():
        raise ApiKeyError(401, "Missing API key. Send `Authorization: Bearer ohdp_...`.")
    caller = resolve_key(engine, presented.strip())
    if caller is None:
        raise ApiKeyError(401, "Invalid or revoked API key.")
    if caller.tier not in API_TIERS:
        log.info("api_refused_not_plus", user=caller.email, tier=caller.tier)
        raise ApiKeyError(
            402,
            "The data API is included with Plus. Subscribe at "
            "https://app.open-health-data-platform.org/billing.",
        )
    return caller


def get_engine(request: Request) -> Engine:
    engine: Engine | None = getattr(request.app.state, "engine", None)
    if engine is None:
        raise HTTPException(503, "The user database is not available.")
    return engine


def require_api_caller(
    request: Request, engine: Annotated[Engine, Depends(get_engine)]
) -> ApiCaller:
    try:
        return authenticate(engine, request.headers.get("authorization"))
    except ApiKeyError as exc:
        raise HTTPException(exc.status, exc.message) from exc


# --- browser routes ---------------------------------------------------------

router = APIRouter(prefix="/api/keys", tags=["api-keys"])


class ApiKey(BaseModel):
    id: int
    name: str
    prefix: str
    created_at: datetime
    last_used_at: datetime | None


class CreateKeyRequest(BaseModel):
    name: str = Field(default="", max_length=100)


class CreatedKey(ApiKey):
    # The only response that ever carries a key's plaintext.
    key: str


@router.get("")
def get_keys(
    user: Annotated[User, Depends(get_current_user)],
    engine: Annotated[Engine, Depends(get_engine)],
) -> list[ApiKey]:
    return [ApiKey.model_validate(row) for row in list_keys(engine, user_email=user.email)]


@router.post("")
def post_key(
    body: CreateKeyRequest,
    user: Annotated[User, Depends(get_current_user)],
    engine: Annotated[Engine, Depends(get_engine)],
) -> CreatedKey:
    """Create a key. Plus only, checked against `users` rather than the session
    for the same reason `resolve_key` is."""
    if current_tier(engine, user.email) not in API_TIERS:
        raise HTTPException(402, "API keys are included with Plus.")
    if active_key_count(engine, user_email=user.email) >= MAX_ACTIVE_KEYS:
        raise HTTPException(
            409, f"You already have {MAX_ACTIVE_KEYS} keys. Revoke one to create another."
        )
    key_id, key = create_key(engine, user_email=user.email, name=body.name.strip())
    created = next(row for row in list_keys(engine, user_email=user.email) if row["id"] == key_id)
    return CreatedKey(**ApiKey.model_validate(created).model_dump(), key=key)


@router.delete("/{key_id}", status_code=204)
def delete_key(
    key_id: int,
    user: Annotated[User, Depends(get_current_user)],
    engine: Annotated[Engine, Depends(get_engine)],
) -> None:
    if not revoke_key(engine, user_email=user.email, key_id=key_id):
        raise HTTPException(404, "No such key.")


def current_tier(engine: Engine, email: str) -> str:
    with engine.connect() as connection:
        tier = connection.execute(select(users.c.tier).where(users.c.email == email)).scalar()
    return str(tier or "free")
