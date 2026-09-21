"""Curated hub content: news, data sources, dashboards, and literature.

News, dashboards, and literature back three of the hub's public pages from
tables here. They are *curated*, not crawled — rows are written by us (seeds
below, an admin surface later), never by users and never by the chat agent, so
what the front page shows cannot be influenced by prompt injection or an
upstream API going strange.

The dashboards here are the curated, published kind. Do not confuse them with
the chat's `render_dashboard` tool (`hub_api.dashboards`, mounted under
`/tools`), which draws a throwaway spec into a single chat turn: that one is
agent-written and never stored, this one is human-written and read by a public
page.

Data sources are the exception: that page reads OpenMetadata's own
Source-aligned domains directly (`ohdp_agent.domains`) rather than a table
here, so a domain added or edited in the catalog shows up with no redeploy —
OpenMetadata is the source of truth for what data sources exist, the same
shift ADR-0019 made for the lakehouse's schema. All four routes are public and
read-only: they are marketing surfaces, and gating them behind sign-in would
defeat them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy import JSON, Boolean, Column, DateTime, Integer, String, Table, select
from sqlalchemy.engine import Engine

from hub_api import db
from ohdp_agent.domains import DomainsClient, DomainsError
from ohdp_shared import get_logger, settings

log = get_logger(__name__)

# All on db.metadata so one `ensure_schema` creates everything hub-api owns.
news_items = Table(
    "news_items",
    db.metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("title", String, nullable=False),
    Column("summary", String, nullable=False),
    # Where "Read more" goes — a study, a dashboard, a docs page.
    Column("url", String, nullable=False),
    # study | visualization | platform
    Column("kind", String(32), nullable=False, default="platform"),
    Column("published_at", DateTime(timezone=True), nullable=False, index=True),
)

curated_dashboards = Table(
    "curated_dashboards",
    db.metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("title", String, nullable=False),
    Column("description", String, nullable=False),
    # A Vega-Lite spec bound server-side (ohdp_agent.dashboard) — the same
    # mechanism the chatbot draws with, so a dashboard on this page is one the
    # agent could reproduce.
    Column("vega", JSON, nullable=False),
    Column("cube_query", JSON, nullable=False),
    Column("featured", Boolean, nullable=False, default=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

curated_literature = Table(
    "curated_literature",
    db.metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("title", String, nullable=False),
    Column("authors", String, nullable=False, default=""),
    Column("venue", String, nullable=False, default=""),
    Column("year", Integer, nullable=True),
    Column("url", String, nullable=False),
    Column("doi", String, nullable=False, default=""),
    Column("summary", String, nullable=False, default=""),
    Column("tags", JSON, nullable=False, default=list),
    Column("trending", Boolean, nullable=False, default=False),
)


router = APIRouter(prefix="/api", tags=["content"])

# ---------------------------------------------------------------- seeds
# First-boot content so the pages are never empty. These insert only when the
# table is empty; after that, rows are managed directly (an admin UI is M4+).

_SEED_NEWS: list[dict[str, Any]] = [
    {
        "title": "Welcome to the Open Health Data Platform",
        "summary": (
            "Population-level public health data — air quality, chronic "
            "disease, drug safety — queryable through one semantic layer, "
            "with a chatbot that can only answer from curated metrics."
        ),
        "url": "https://github.com/kevinmcquate/open-health-data-platform",
        "kind": "platform",
    },
]


def seed_if_empty(engine: Engine) -> None:
    """Insert starter rows into any content table that is empty."""
    now = datetime.now(UTC)
    with engine.begin() as connection:
        if not connection.execute(select(news_items.c.id)).first():
            connection.execute(
                news_items.insert(),
                [{**item, "published_at": now} for item in _SEED_NEWS],
            )


def _rows(request: Request, table: Table, *order_by: Any) -> list[dict[str, Any]]:
    """Public content must degrade, not 503: no database means empty lists."""
    engine: Engine | None = getattr(request.app.state, "engine", None)
    if engine is None:
        return []
    statement = select(table)
    if order_by:
        statement = statement.order_by(*order_by)
    with engine.connect() as connection:
        return [dict(row) for row in connection.execute(statement).mappings()]


@router.get("/news")
def list_news(request: Request) -> list[dict[str, Any]]:
    return _rows(request, news_items, news_items.c.published_at.desc())


@router.get("/data-sources")
async def list_data_sources() -> list[dict[str, Any]]:
    """Source-aligned OpenMetadata domains, not a curated table — see the
    module docstring. Degrades to `[]` on any OpenMetadata trouble, same as
    `_rows` does for a missing database: a public marketing page must not 503
    because the catalog is unreachable."""
    if not settings.openmetadata_jwt:
        return []
    client = DomainsClient(
        settings.openmetadata_url,
        settings.openmetadata_jwt,
        link_base_url=settings.openmetadata_public_url,
    )
    try:
        domains = await client.list_source_aligned()
    except DomainsError as exc:
        log.warning("data_sources_unavailable", error=str(exc))
        return []
    return [
        {
            "id": domain.id,
            "name": domain.name,
            "description": domain.description,
            "catalog_url": domain.catalog_url,
        }
        for domain in domains
    ]


@router.get("/dashboards")
def list_dashboards(request: Request) -> list[dict[str, Any]]:
    return _rows(
        request,
        curated_dashboards,
        curated_dashboards.c.featured.desc(),
        curated_dashboards.c.created_at.desc(),
    )


@router.get("/literature")
def list_literature(request: Request) -> list[dict[str, Any]]:
    return _rows(
        request,
        curated_literature,
        curated_literature.c.trending.desc(),
        curated_literature.c.year.desc(),
    )
