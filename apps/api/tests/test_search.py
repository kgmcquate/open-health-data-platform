"""`GET /api/search` (hub_api.content) — the header search bar's one call.

What these hold is the reason the route exists at all rather than the browser
just querying OpenMetadata: the hub's searchable content lives in two places.
Topics and catalog assets are OM's; dashboards and literature are rows in this
service's own database and are not in the catalog at any point. So the route
fans out, and — being a public, unauthenticated surface like the topic routes —
each half has to degrade on its own rather than take the dropdown down with it.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

from hub_api import content, db, library
from hub_api.main import app
from ohdp_agent.domains import CatalogAsset, Domain, DomainsError
from ohdp_shared import settings

TOPICS = [
    Domain(
        id="1",
        name="Respiratory",
        description="RSV, influenza and COVID-19 burden.",
        catalog_url="http://om/domain/Respiratory",
    ),
    Domain(
        id="2",
        name="Chronic Disease",
        description="Diabetes and cardiovascular prevalence.",
        catalog_url="http://om/domain/Chronic%20Disease",
    ),
]

ASSET = CatalogAsset(
    id="a1",
    name="NNDSS_WEEKLY",
    description="NNDSS weekly case counts.",
    entity_type="table",
    fqn="snowflake.CURATED.INFECTIOUS_DISEASE.NNDSS_WEEKLY",
    catalog_url="http://om/table/snowflake.CURATED.INFECTIOUS_DISEASE.NNDSS_WEEKLY",
)

SPEC: dict[str, Any] = {
    "name": "flu-ed-visits",
    "title": "Influenza ED visits",
    "description": "Weekly share of emergency department visits.",
    "query": {"measures": ["ed_visits.avg_percent"]},
    "vega_lite": {"mark": "line"},
}


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    made: Engine = db.make_engine(f"sqlite:///{tmp_path}/search.db")
    db.ensure_schema(made)
    app.state.engine = made
    try:
        yield made
    finally:
        app.state.engine = None


@pytest.fixture
def client(engine: Engine) -> TestClient:
    return TestClient(app)


@pytest.fixture
def catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    """OM configured and answering: two topics, one asset for any query."""
    monkeypatch.setattr(settings, "openmetadata_jwt", "s3cret")

    async def fake_list(self: object, domain_type: str) -> list[Domain]:
        return TOPICS

    async def fake_search(self: object, text: str, *, limit: int = 10) -> list[CatalogAsset]:
        return [ASSET]

    monkeypatch.setattr("ohdp_agent.domains.DomainsClient.list_by_type", fake_list)
    monkeypatch.setattr("ohdp_agent.domains.DomainsClient.search", fake_search)


def _dashboard(engine: Engine, **values: Any) -> None:
    now = datetime.now(UTC)
    defaults = {
        "name": SPEC["name"],
        "title": SPEC["title"],
        "description": SPEC["description"],
        "spec": SPEC,
        "html": "<!DOCTYPE html><html><body>chart</body></html>",
        "last_rendered": now,
        "source": "curated",
        "topics": ["Respiratory"],
        "featured": False,
        "upvotes": 0,
        "downvotes": 0,
        "created_at": now,
        "updated_at": now,
    }
    with engine.begin() as connection:
        connection.execute(library.dashboards.insert().values({**defaults, **values}))


def _paper(engine: Engine, **values: Any) -> None:
    defaults = {
        "title": "Influenza vaccine effectiveness, 2025-26",
        "authors": "Okafor, Lindqvist",
        "venue": "NEJM",
        "year": 2026,
        "url": "https://example.org/flu-ve",
        "doi": "10.0000/flu-ve",
        "summary": "Interim estimates against influenza A.",
        "tags": ["Respiratory"],
        "trending": True,
    }
    with engine.begin() as connection:
        connection.execute(content.curated_literature.insert().values({**defaults, **values}))


# --- the fan-out -----------------------------------------------------------


def test_one_query_returns_all_four_kinds(
    client: TestClient, engine: Engine, catalog: None
) -> None:
    """The whole point of the route: catalog and hub content in one answer."""
    _dashboard(engine)
    _paper(engine)

    body = client.get("/api/search", params={"q": "influenza"}).json()

    assert [topic["name"] for topic in body["topics"]] == ["Respiratory"]
    assert [asset["name"] for asset in body["assets"]] == ["NNDSS_WEEKLY"]
    assert [d["title"] for d in body["dashboards"]] == ["Influenza ED visits"]
    assert [p["venue"] for p in body["literature"]] == ["NEJM"]


def test_every_word_has_to_match(client: TestClient, engine: Engine) -> None:
    """AND, not OR — adding a word narrows a search."""
    _dashboard(engine)

    matched = client.get("/api/search", params={"q": "influenza visits"}).json()
    missed = client.get("/api/search", params={"q": "influenza wastewater"}).json()

    assert len(matched["dashboards"]) == 1
    assert missed["dashboards"] == []


def test_a_dashboard_matches_on_its_topic(client: TestClient, engine: Engine) -> None:
    """Searching a topic name finds the dashboards filed under it, not just
    the ones with the word in their title."""
    _dashboard(engine, title="Weekly ED share", description="")

    body = client.get("/api/search", params={"q": "respiratory"}).json()

    assert [d["title"] for d in body["dashboards"]] == ["Weekly ED share"]


def test_a_paper_matches_on_author_and_tag(client: TestClient, engine: Engine) -> None:
    _paper(engine)

    by_author = client.get("/api/search", params={"q": "lindqvist"}).json()
    by_tag = client.get("/api/search", params={"q": "respiratory"}).json()

    assert len(by_author["literature"]) == 1
    assert len(by_tag["literature"]) == 1


def test_a_hidden_dashboard_is_not_searchable(client: TestClient, engine: Engine) -> None:
    """Search reads `library.public_rows`, so a dashboard voted below
    `HIDE_AT_SCORE` is as absent here as it is on the Dashboards page —
    otherwise search would be a way around the hiding."""
    _dashboard(engine, downvotes=10)

    body = client.get("/api/search", params={"q": "influenza"}).json()

    assert body["dashboards"] == []


def test_an_empty_query_matches_nothing(client: TestClient, engine: Engine, catalog: None) -> None:
    """Not "everything": a blank box is not a request for the whole catalog."""
    _dashboard(engine)
    _paper(engine)

    body = client.get("/api/search", params={"q": "   "}).json()

    assert body == {"query": "   ", "topics": [], "assets": [], "dashboards": [], "literature": []}


# --- degrading -------------------------------------------------------------


def test_no_catalog_configured_still_searches_the_hub(
    client: TestClient, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The half of the answer that does not need OM must survive OM being
    absent — this is a public page, and a 503 would be the whole dropdown."""
    monkeypatch.setattr(settings, "openmetadata_jwt", "")
    _dashboard(engine)
    _paper(engine)

    response = client.get("/api/search", params={"q": "influenza"})

    assert response.status_code == 200
    body = response.json()
    assert body["topics"] == [] and body["assets"] == []
    assert len(body["dashboards"]) == 1
    assert len(body["literature"]) == 1


def test_an_erroring_catalog_does_not_blank_the_other_sections(
    client: TestClient, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "openmetadata_jwt", "s3cret")

    async def boom(self: object, *args: Any, **kwargs: Any) -> Any:
        raise DomainsError("OpenMetadata returned 503")

    monkeypatch.setattr("ohdp_agent.domains.DomainsClient.list_by_type", boom)
    monkeypatch.setattr("ohdp_agent.domains.DomainsClient.search", boom)
    _dashboard(engine)

    response = client.get("/api/search", params={"q": "influenza"})

    assert response.status_code == 200
    body = response.json()
    assert body["topics"] == [] and body["assets"] == []
    assert len(body["dashboards"]) == 1


def test_a_failing_asset_search_keeps_the_topics(
    client: TestClient, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The two catalog sections are two calls, and they degrade separately."""
    monkeypatch.setattr(settings, "openmetadata_jwt", "s3cret")

    async def fake_list(self: object, domain_type: str) -> list[Domain]:
        return TOPICS

    async def boom(self: object, text: str, *, limit: int = 10) -> Any:
        raise DomainsError("OpenMetadata returned 503")

    monkeypatch.setattr("ohdp_agent.domains.DomainsClient.list_by_type", fake_list)
    monkeypatch.setattr("ohdp_agent.domains.DomainsClient.search", boom)

    body = client.get("/api/search", params={"q": "respiratory"}).json()

    assert [topic["name"] for topic in body["topics"]] == ["Respiratory"]
    assert body["assets"] == []


def test_no_database_still_searches_the_catalog(
    catalog: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The mirror of the case above — Postgres down, catalog up."""
    app.state.engine = None
    try:
        body = TestClient(app).get("/api/search", params={"q": "respiratory"}).json()
    finally:
        app.state.engine = None

    assert [topic["name"] for topic in body["topics"]] == ["Respiratory"]
    assert body["dashboards"] == [] and body["literature"] == []


# --- limits ----------------------------------------------------------------


def test_the_default_limit_caps_each_section(client: TestClient, engine: Engine) -> None:
    """The dropdown's cap, per section (`content.SEARCH_LIMIT`)."""
    for index in range(content.SEARCH_LIMIT + 3):
        _dashboard(engine, name=f"flu-{index}", title=f"Influenza {index}")

    body = client.get("/api/search", params={"q": "influenza"}).json()

    assert len(body["dashboards"]) == content.SEARCH_LIMIT


def test_the_search_page_can_ask_for_more(client: TestClient, engine: Engine) -> None:
    for index in range(content.SEARCH_LIMIT + 3):
        _dashboard(engine, name=f"flu-{index}", title=f"Influenza {index}")

    body = client.get("/api/search", params={"q": "influenza", "limit": 50}).json()

    assert len(body["dashboards"]) == content.SEARCH_LIMIT + 3


def test_the_limit_has_a_ceiling(client: TestClient) -> None:
    """An open `limit` would turn a public route into "dump the catalog"."""
    response = client.get("/api/search", params={"q": "influenza", "limit": 5000})

    assert response.status_code == 422
