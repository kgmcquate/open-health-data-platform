"""Curated hub content: topics, dashboards, and literature.

Dashboards and literature back two of the hub's public pages from tables
here. They are *curated*, not crawled — rows are written by us (an admin
surface later) or by the Dagster pipeline's trending-literature sync
(`POST /api/internal/literature/trending`, below), never by users and never
by the chat agent, so what a topic page shows cannot be influenced by prompt
injection or an upstream API going strange.

The dashboards here are the curated, published kind. Do not confuse them with
the chat's `render_dashboard` tool (`hub_api.dashboards`, mounted under
`/tools`), which draws a throwaway spec into a single chat turn: that one is
agent-written and never stored, this one is human-written and read by a public
page.

Topics are the exception: that page reads OpenMetadata's own Consumer-aligned
domains directly (`ohdp_agent.domains`) rather than a table here, so a topic
added or edited in the catalog shows up with no redeploy — OpenMetadata is
the source of truth for what topics, metrics and data assets exist, the same
shift ADR-0019 made for the lakehouse's schema. The public GET routes are all
read-only: they are marketing surfaces, and gating them behind sign-in would
defeat them. `POST /api/internal/literature/trending` is the one write route
in this module and is not public — see its own docstring.

**Why the trending sync writes over HTTP rather than opening its own
connection to this database:** hub-api is the only thing that writes to the
`app` Postgres database anywhere in this codebase (every table on
`db.metadata` — chat log, threads, and the two tables below — is written
only from this process). ``ohdp_orchestration.assets.literature_trending_sync``
(Dagster) stays consistent with that rather than becoming a second writer with
its own credentials to this database: it calls the internal route below over
the cluster network instead, authenticated with a shared bearer token (same
pattern as ``OHDP_CUBE_API_SECRET`` — see
``platform/helm/charts/platform-base/templates/secrets.yaml``).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Integer,
    String,
    Table,
    inspect,
    select,
    text,
    update,
)
from sqlalchemy.engine import Engine

from hub_api import db
from ohdp_agent.domains import CatalogAsset, Domain, DomainsClient, DomainsError
from ohdp_shared import get_logger, settings

log = get_logger(__name__)

# All on db.metadata so one `ensure_schema` creates everything hub-api owns.
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
    # The Consumer-aligned domain (topic) name this dashboard is attached to,
    # e.g. "Infectious Disease" — nullable because a dashboard needn't belong
    # to exactly one topic. Added after the table already existed in some
    # databases; see `ensure_columns_and_indexes` below.
    Column("topic", String, nullable=True, index=True),
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
    # Topic names this paper is relevant to — literature_trending_sync writes
    # the Consumer-aligned domain(s) it was ranked under, so this doubles as
    # the join key `/api/literature?topic=` filters on below.
    Column("tags", JSON, nullable=False, default=list),
    Column("trending", Boolean, nullable=False, default=False),
)


router = APIRouter(prefix="/api", tags=["content"])


def ensure_columns_and_indexes(engine: Engine) -> None:
    """Schema fixes for a database whose tables predate a column or index —
    same problem `db.py`'s `_add_missing_columns` solves for `chat_turns`:
    `metadata.create_all` only creates *missing* tables, so it never adds a
    column to a `curated_*` table that already exists. The column check goes
    through `inspect()` rather than `ADD COLUMN IF NOT EXISTS` because SQLite
    (used by the test suite) never learned that syntax — `_add_missing_columns`
    hits the same wall for the same reason. `CREATE INDEX IF NOT EXISTS` has
    no such gap, so the indexes stay plain SQL, safe to run on every startup
    including a fresh database (where `create_all` already added them as part
    of the columns' own definitions).
    """
    inspector = inspect(engine)
    dashboard_columns = {col["name"] for col in inspector.get_columns("curated_dashboards")}
    with engine.begin() as connection:
        if "topic" not in dashboard_columns:
            connection.execute(text("ALTER TABLE curated_dashboards ADD COLUMN topic VARCHAR"))
        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_curated_literature_url "
                "ON curated_literature (url)"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_curated_dashboards_topic "
                "ON curated_dashboards (topic)"
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


def _domains_client() -> DomainsClient | None:
    """`None` when OpenMetadata isn't configured — every route below treats
    that exactly like a reachable-but-erroring catalog: an empty result, not
    a 503, since this is a marketing surface."""
    if not settings.openmetadata_jwt:
        return None
    return DomainsClient(
        settings.openmetadata_url,
        settings.openmetadata_jwt,
        link_base_url=settings.openmetadata_public_url,
    )


@router.get("/dashboards")
def list_dashboards(
    request: Request, topic: str | None = Query(default=None)
) -> list[dict[str, Any]]:
    rows = _rows(
        request,
        curated_dashboards,
        curated_dashboards.c.featured.desc(),
        curated_dashboards.c.created_at.desc(),
    )
    if topic is not None:
        rows = [row for row in rows if row.get("topic") == topic]
    return rows


@router.get("/literature")
def list_literature(
    request: Request, topic: str | None = Query(default=None)
) -> list[dict[str, Any]]:
    rows = _rows(
        request,
        curated_literature,
        curated_literature.c.trending.desc(),
        curated_literature.c.year.desc(),
    )
    if topic is not None:
        # `tags` is a plain JSON column (not JSONB containment) — filtering in
        # Python is simplest at the curated table's current size (~100 rows);
        # revisit if that grows enough to want a database-side index.
        rows = [row for row in rows if topic in (row.get("tags") or [])]
    return rows


@router.get("/topics")
async def list_topics() -> list[dict[str, Any]]:
    """Consumer-aligned OpenMetadata domains — the subject areas a visitor
    browses by, as opposed to the Source-aligned domains that name a
    provider (see `ohdp_agent.domains`'s module docstring). Degrades to `[]`
    on any OpenMetadata trouble: a public marketing page must not 503 because
    the catalog is unreachable."""
    client = _domains_client()
    if client is None:
        return []
    try:
        topics = await client.list_by_type("Consumer-aligned")
    except DomainsError as exc:
        log.warning("topics_unavailable", error=str(exc))
        return []
    return [
        {
            "id": topic.id,
            "name": topic.name,
            "description": topic.description,
            "catalog_url": topic.catalog_url,
        }
        for topic in topics
    ]


@router.get("/topics/{name}")
async def get_topic(name: str) -> dict[str, Any]:
    """One topic's full bundle: the domain itself, its metrics and data
    assets, and the upstream (Source-aligned) providers those assets came
    from. Each section degrades to `[]` independently — a slow or partial
    catalog response should never blank the whole page over one section."""
    client = _domains_client()
    if client is None:
        raise HTTPException(503, "Catalog is not configured.")

    try:
        topics = await client.list_by_type("Consumer-aligned")
    except DomainsError as exc:
        log.warning("topic_unavailable", name=name, error=str(exc))
        raise HTTPException(503, "Catalog is unavailable.") from exc

    topic = next((t for t in topics if t.name == name), None)
    if topic is None:
        raise HTTPException(404, "No such topic.")

    async def _assets(entity_type: str) -> list[CatalogAsset]:
        try:
            return await client.assets_in_domain(name, entity_type)
        except DomainsError as exc:
            log.warning(
                "topic_assets_unavailable", name=name, entity_type=entity_type, error=str(exc)
            )
            return []

    metrics = await _assets("metric")
    tables = await _assets("table")

    try:
        sources = await client.upstream_sources([table.fqn for table in tables])
    except DomainsError as exc:
        log.warning("topic_sources_unavailable", name=name, error=str(exc))
        sources = []

    def _asset_dict(asset: CatalogAsset) -> dict[str, Any]:
        return {
            "id": asset.id,
            "name": asset.name,
            "description": asset.description,
            "entity_type": asset.entity_type,
            "catalog_url": asset.catalog_url,
        }

    def _domain_dict(domain: Domain) -> dict[str, Any]:
        return {
            "id": domain.id,
            "name": domain.name,
            "description": domain.description,
            "catalog_url": domain.catalog_url,
        }

    return {
        "topic": _domain_dict(topic),
        "metrics": [_asset_dict(m) for m in metrics],
        "assets": [_asset_dict(t) for t in tables],
        "sources": [_domain_dict(s) for s in sources],
    }


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
