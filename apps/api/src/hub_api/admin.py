"""Admin console: every signed-up user, their tier, and today's usage.

Read-only for now — there is nothing here to change yet, only to see (tier
changes come from the billing webhook once M4 exists; the admin role itself
is a deploy, per `hub_api.auth.is_admin`). Usage is computed the same way
`GET /api/me` computes it for the caller themselves — COUNT/SUM over
`chat_turns`, since that log is the one source of truth for usage
(`hub_api.db`'s own docstring) — just run once per row instead of once for
the signed-in user. The per-tier allowance numbers are `hub_api.chat`'s, not
re-declared here, so there is nowhere for the two to drift apart.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.engine import Engine

from hub_api import auth, db
from hub_api.chat import (
    question_allowance,
    render_allowance,
    save_allowance,
    token_allowance,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])


class AdminUser(BaseModel):
    email: str
    name: str
    tier: str
    created_at: datetime
    last_login_at: datetime
    questions_used_today: int
    questions_allowed_per_day: int
    tokens_used_today: int
    tokens_allowed_per_day: int
    renders_used_today: int
    renders_allowed_per_day: int
    saves_used_today: int
    saves_allowed_per_day: int


def get_engine(request: Request) -> Engine:
    engine: Engine | None = getattr(request.app.state, "engine", None)
    if engine is None:
        raise HTTPException(503, "The user database is not available.")
    return engine


@router.get("/users")
def list_users(
    _admin: Annotated[auth.User, Depends(auth.require_admin)],
    engine: Annotated[Engine, Depends(get_engine)],
) -> list[AdminUser]:
    """Every user, most recently logged-in first, with their tier and today's
    usage against it.

    One round trip per usage dimension per user — the same cost `GET /api/me`
    already pays for one user, just paid once per row here. Fine at the
    traffic and user counts an admin console sees; an aggregated query is the
    fix if that stops being true.
    """
    return [
        AdminUser(
            email=str(row["email"]),
            name=str(row["name"]),
            tier=(tier := str(row["tier"])),
            created_at=row["created_at"],
            last_login_at=row["last_login_at"],
            questions_used_today=db.questions_today(engine, str(row["email"])),
            questions_allowed_per_day=question_allowance(tier),
            tokens_used_today=db.tokens_today(engine, str(row["email"])),
            tokens_allowed_per_day=token_allowance(tier),
            renders_used_today=db.tool_calls_today(engine, str(row["email"]), "render_dashboard"),
            renders_allowed_per_day=render_allowance(tier),
            saves_used_today=db.tool_calls_today(engine, str(row["email"]), "save_dashboard"),
            saves_allowed_per_day=save_allowance(tier),
        )
        for row in auth.list_users(engine)
    ]
