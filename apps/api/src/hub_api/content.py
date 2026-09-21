"""Curated hub content: news, data sources, dashboards, and literature.

News, dashboards, and literature back three of the hub's public pages from
tables here. They are *curated*, not crawled — rows are written by us (seeds
below, an admin surface later) or by the Dagster pipeline's trending-literature
sync (`POST /api/internal/literature/trending`, below), never by users and
never by the chat agent, so what the front page shows cannot be influenced by
prompt injection or an upstream API going strange.

The dashboards here are the curated, published kind. Do not confuse them with
the chat's `render_dashboard` tool (`hub_api.dashboards`, mounted under
`/tools`), which draws a throwaway spec into a single chat turn: that one is
agent-written and never stored, this one is human-written and read by a public
page.

Data sources are the exception: that page reads OpenMetadata's own
Source-aligned domains directly (`ohdp_agent.domains`) rather than a table
here, so a domain added or edited in the catalog shows up with no redeploy —
OpenMetadata is the source of truth for what data sources exist, the same
shift ADR-0019 made for the lakehouse's schema. The public GET routes are all
read-only: they are marketing surfaces, and gating them behind sign-in would
defeat them. `POST /api/internal/literature/trending` is the one write route
in this module and is not public — see its own docstring.

**Why the trending sync writes over HTTP rather than opening its own
connection to this database:** hub-api is the only thing that writes to the
`app` Postgres database anywhere in this codebase (every table on
`db.metadata` — chat log, threads, and the three tables below — is written
only from this process). ``ohdp_orchestration.assets.literature_trending_sync``
(Dagster) stays consistent with that rather than becoming a second writer with
its own credentials to this database: it calls the internal route below over
the cluster network instead, authenticated with a shared bearer token (same
pattern as ``OHDP_CUBE_API_SECRET`` — see
``platform/helm/charts/platform-base/templates/secrets.yaml``).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import JSON, Boolean, Column, DateTime, Integer, String, Table, select, text, update
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
    # unique: the natural key the trending sync upserts on (one row per paper,
    # never a duplicate across runs) — see `upsert_trending_literature` below.
    Column("url", String, nullable=False, unique=True),
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


def ensure_indexes(engine: Engine) -> None:
    """``curated_literature.url``'s unique constraint, for any database whose
    table predates it.

    Same problem `db.py`'s `_add_missing_columns` solves for `chat_turns`:
    `metadata.create_all` only creates *missing* tables, so a database that
    already has `curated_literature` from before the trending sync existed
    never gets this constraint from `create_all` alone. `IF NOT EXISTS` makes
    it safe to run on every startup, on a fresh database too (where
    `create_all` already added it as part of the column's `unique=True`).
    """
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_curated_literature_url "
                "ON curated_literature (url)"
            )
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


class TrendingLiteratureItem(BaseModel):
    """One paper from ``ohdp_orchestration.assets.literature_trending_sync``'s
    per-Domain OpenAlex ranking — shape mirrors ``curated_literature``'s
    columns directly, `trending` excluded since every item this route
    receives is trending by construction (see the route's own docstring)."""

    title: str
    authors: str = ""
    venue: str = ""
    year: int | None = None
    url: str
    doi: str = ""
    summary: str = ""
    tags: list[str] = []


def _require_ingest_token(
    # include_in_schema=False: an infrastructure credential, not something a
    # caller ever fills in through the OpenAPI form — same reasoning as
    # `hub_api.issues.get_reporter`'s identical `authorization` parameter.
    authorization: Annotated[str | None, Header(include_in_schema=False)] = None,
) -> None:
    token = settings.internal_ingest_token
    scheme, _, presented = (authorization or "").partition(" ")
    if not token or scheme.lower() != "bearer" or presented.strip() != token:
        raise HTTPException(401, "Not authorized.")


@router.post("/internal/literature/trending", include_in_schema=False)
def replace_trending_literature(
    request: Request,
    items: list[TrendingLiteratureItem],
    _: Annotated[None, Depends(_require_ingest_token)],
) -> dict[str, int]:
    """Called once per run by the Dagster trending-literature sync with that
    run's **complete** trending set (every Domain's selection combined) — not
    a public route (``include_in_schema=False``, and gated by
    ``_require_ingest_token``'s shared bearer, see the module docstring for
    why this is an HTTP call rather than a direct database connection from
    Dagster).

    Upserts each item by its unique ``url`` and sets ``trending=True`` on it;
    any existing row that *was* trending but is absent from this run's set has
    ``trending`` cleared rather than being deleted — a paper that ages out of
    the ranking should stop showing the 🔥 badge, not vanish from the page if
    something else (a future curation pass) still references it.
    """
    engine: Engine | None = getattr(request.app.state, "engine", None)
    if engine is None:
        raise HTTPException(503, "Database is not available.")

    urls = [item.url for item in items]
    with engine.begin() as connection:
        for item in items:
            existing = connection.execute(
                select(curated_literature.c.id).where(curated_literature.c.url == item.url)
            ).first()
            values = {**item.model_dump(), "trending": True}
            if existing:
                connection.execute(
                    update(curated_literature)
                    .where(curated_literature.c.id == existing.id)
                    .values(**values)
                )
            else:
                connection.execute(curated_literature.insert().values(**values))

        clear_stale = update(curated_literature).where(curated_literature.c.trending.is_(True))
        if urls:
            clear_stale = clear_stale.where(curated_literature.c.url.notin_(urls))
        connection.execute(clear_stale.values(trending=False))

    log.info("trending_literature_replaced", count=len(items))
    return {"synced": len(items)}
