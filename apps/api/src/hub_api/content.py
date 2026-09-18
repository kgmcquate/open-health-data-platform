"""Curated hub content: news, data sources, plots, and literature.

These four tables back the hub's public pages. They are *curated*, not
crawled — rows are written by us (seeds below, an admin surface later), never
by users and never by the chat agent, so what the front page shows cannot be
influenced by prompt injection or an upstream API going strange. All four
routes are public and read-only: they are marketing surfaces, and gating them
behind sign-in would defeat them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy import JSON, Boolean, Column, DateTime, Integer, String, Table, select
from sqlalchemy.engine import Engine

from hub_api import db

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

data_sources = Table(
    "data_sources",
    db.metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", String, nullable=False, unique=True),
    Column("provider", String, nullable=False),
    Column("description", String, nullable=False),
    Column("homepage_url", String, nullable=False, default=""),
    # Deep link into the OpenMetadata UI for this source's tables.
    Column("catalog_url", String, nullable=False, default=""),
    Column("tags", JSON, nullable=False, default=list),
)

curated_plots = Table(
    "curated_plots",
    db.metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("title", String, nullable=False),
    Column("description", String, nullable=False),
    # A Vega-Lite spec bound server-side (ohdp_agent.dashboard) — the same
    # mechanism the chatbot draws with, so a plot on this page is one the
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

_SEED_SOURCES: list[dict[str, Any]] = [
    {
        "name": "OpenAQ",
        "provider": "OpenAQ",
        "description": "Ground-level air quality measurements from stations worldwide.",
        "homepage_url": "https://openaq.org",
        "tags": ["air quality", "environmental"],
    },
    {
        "name": "CDC PLACES / Chronic Data",
        "provider": "CDC",
        "description": "Model-based estimates of chronic disease measures across the US.",
        "homepage_url": "https://www.cdc.gov/places",
        "tags": ["chronic disease", "surveillance"],
    },
    {
        "name": "openFDA",
        "provider": "US FDA",
        "description": "Drug adverse events, recalls, and labeling from the FDA's public APIs.",
        "homepage_url": "https://open.fda.gov",
        "tags": ["drug safety"],
    },
    {
        "name": "WHO GHO",
        "provider": "World Health Organization",
        "description": "Global Health Observatory indicators across member states.",
        "homepage_url": "https://www.who.int/data/gho",
        "tags": ["global health", "indicators"],
    },
    {
        "name": "CMS",
        "provider": "Centers for Medicare & Medicaid Services",
        "description": "US healthcare utilization and spending open data.",
        "homepage_url": "https://data.cms.gov",
        "tags": ["utilization", "spending"],
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
        if not connection.execute(select(data_sources.c.id)).first():
            connection.execute(data_sources.insert(), _SEED_SOURCES)


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
def list_data_sources(request: Request) -> list[dict[str, Any]]:
    return _rows(request, data_sources, data_sources.c.name)


@router.get("/plots")
def list_plots(request: Request) -> list[dict[str, Any]]:
    return _rows(
        request,
        curated_plots,
        curated_plots.c.featured.desc(),
        curated_plots.c.created_at.desc(),
    )


@router.get("/literature")
def list_literature(request: Request) -> list[dict[str, Any]]:
    return _rows(
        request,
        curated_literature,
        curated_literature.c.trending.desc(),
        curated_literature.c.year.desc(),
    )
