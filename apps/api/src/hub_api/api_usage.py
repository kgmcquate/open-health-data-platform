"""Monthly allowances for the paid data API (`hub_api.gateway`, ADR-0030).

One row in `api_usage` per call that costs something, and the count since the
start of the month is the quota. It's the same design as the chat quotas in
`hub_api.db`: the log is the counter, so there's no second number to drift.

Surfaces draw from pools. Cube's REST API has its own pool, and the two MCP
endpoints (Cube and catalog) share one, because to an MCP client they're the
same kind of call and one "MCP calls" number is what a user can reason about.

The month is the calendar month in UTC, not the Stripe billing period. It's
simpler to explain ("resets on the 1st") and doesn't depend on a
`subscriptions` row existing. Over the allowance, a call is refused. There is
no overage billing yet.

Like the chat gate, this checks and then inserts, so a burst of simultaneous
calls can overrun the allowance by a few. A reservation row would close that
gap; at these volumes it isn't worth one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends
from sqlalchemy import JSON, Column, DateTime, Integer, String, Table, delete, func, select
from sqlalchemy.engine import Engine

from hub_api import db
from hub_api.api_keys import API_TIERS, ApiCaller, current_tier, get_engine
from hub_api.auth import User, get_current_user
from ohdp_shared import get_logger, settings

log = get_logger(__name__)

Surface = Literal["cube", "cube_mcp", "catalog_mcp"]
Pool = Literal["cube", "mcp"]

POOL_OF: dict[Surface, Pool] = {"cube": "cube", "cube_mcp": "mcp", "catalog_mcp": "mcp"}
POOLS: tuple[Pool, ...] = ("cube", "mcp")

api_usage = Table(
    "api_usage",
    db.metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("created_at", DateTime(timezone=True), nullable=False, index=True),
    Column("user_email", String(320), nullable=False, index=True),
    Column("key_id", Integer, nullable=False),
    Column("surface", String(16), nullable=False),
    # What was called: the Cube members or the MCP tool name. For support
    # questions ("what used up my allowance?"), never for counting.
    Column("detail", JSON, nullable=False),
    sqlite_autoincrement=True,
)


def api_allowance(tier: str, pool: Pool) -> int:
    """A tier's monthly allowance for one pool. 0 for any tier without the API.
    Public, like `hub_api.chat.question_allowance`, so the admin console and
    the Developer page show the number the gate enforces."""
    if tier not in API_TIERS:
        return 0
    return settings.plus_monthly_cube_queries if pool == "cube" else settings.plus_monthly_mcp_calls


def start_of_month(now: datetime | None = None) -> datetime:
    now = now or datetime.now(UTC)
    return now.astimezone(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def start_of_next_month(now: datetime | None = None) -> datetime:
    first = start_of_month(now)
    return (first + timedelta(days=32)).replace(day=1)


def used_this_month(engine: Engine, user_email: str, pool: Pool) -> int:
    surfaces = [surface for surface, owner in POOL_OF.items() if owner == pool]
    statement = (
        select(func.count())
        .select_from(api_usage)
        .where(api_usage.c.user_email == user_email)
        .where(api_usage.c.surface.in_(surfaces))
        .where(api_usage.c.created_at >= start_of_month())
    )
    with engine.connect() as connection:
        return int(connection.execute(statement).scalar_one())


# Not frozen: Python sets `__traceback__` on an exception as it propagates,
# and a frozen dataclass refuses that assignment.
@dataclass(eq=False)
class QuotaExceeded(Exception):
    """The caller has used their allowance for this pool this month."""

    pool: Pool
    used: int
    allowance: int
    resets_at: datetime

    @property
    def message(self) -> str:
        noun = "Cube queries" if self.pool == "cube" else "MCP tool calls"
        return (
            f"You have used all {self.allowance:,} {noun} included with Plus this month. "
            f"The allowance resets at {self.resets_at.isoformat()}."
        )

    def as_json(self) -> dict[str, Any]:
        return {
            "detail": self.message,
            "pool": self.pool,
            "used": self.used,
            "allowance": self.allowance,
            "resets_at": self.resets_at.isoformat(),
        }


def charge(engine: Engine, caller: ApiCaller, surface: Surface, detail: dict[str, Any]) -> None:
    """Record one unit against the caller's allowance, or raise `QuotaExceeded`.

    Called before the call is forwarded, so a call that then fails still
    counts. Counting only successes would make a query that Cube rejects free
    to retry forever.
    """
    pool = POOL_OF[surface]
    allowance = api_allowance(caller.tier, pool)
    used = used_this_month(engine, caller.email, pool)
    if used >= allowance:
        log.info("api_quota_exceeded", user=caller.email, pool=pool, used=used, allowance=allowance)
        raise QuotaExceeded(pool, used, allowance, start_of_next_month())
    with engine.begin() as connection:
        connection.execute(
            api_usage.insert().values(
                created_at=datetime.now(UTC),
                user_email=caller.email,
                key_id=caller.key_id,
                surface=surface,
                detail=detail,
            )
        )


def usage_summary(engine: Engine, user_email: str, tier: str) -> dict[str, dict[str, int]]:
    """This month's usage against the allowance, per pool."""
    return {
        pool: {
            "used": used_this_month(engine, user_email, pool),
            "allowance": api_allowance(tier, pool),
        }
        for pool in POOLS
    }


def purge_expired(engine: Engine, *, retention_days: int) -> None:
    """Delete usage rows older than the retention window the privacy policy
    states, alongside `db.purge_expired`. The quota only reads the current
    month, so this never changes a count."""
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    with engine.begin() as connection:
        result = connection.execute(delete(api_usage).where(api_usage.c.created_at < cutoff))
    log.info("api_usage_purged", rows=result.rowcount)


# --- browser route ----------------------------------------------------------

router = APIRouter(prefix="/api/keys", tags=["api-keys"])


@router.get("/usage")
def get_usage(
    user: Annotated[User, Depends(get_current_user)],
    engine: Annotated[Engine, Depends(get_engine)],
) -> dict[str, Any]:
    """The Developer page's usage bars: this month's calls per pool, the
    allowance the gate enforces, and when it resets. Tier comes from `users`,
    matching what a key would be charged as right now."""
    tier = current_tier(engine, user.email)
    return {
        "tier": tier,
        "pools": usage_summary(engine, user.email, tier),
        "resets_at": start_of_next_month().isoformat(),
    }
