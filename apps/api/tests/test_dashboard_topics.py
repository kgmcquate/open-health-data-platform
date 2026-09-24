"""The topics a save files a dashboard under.

This is the one part of saving that reaches outside the platform, and it fails
quietly if it fails at all: a topic lookup that silently returns nothing leaves
every dashboard off every topic page, with no error anywhere to notice.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hub_api import db, issues, library
from hub_api.main import app
from ohdp_agent.domains import DomainsError
from ohdp_shared import settings

SPEC: dict[str, Any] = {
    "name": "ed-visits",
    "title": "ED visit share",
    "query": {"measures": ["respiratory__ed_visit_share_weekly.avg_percent"]},
    "vega_lite": {
        "mark": "line",
        "encoding": {
            "y": {"field": "respiratory__ed_visit_share_weekly.avg_percent", "type": "quantitative"}
        },
    },
}

ROWS = [{"respiratory__ed_visit_share_weekly.avg_percent": 3.1}]


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    engine = db.make_engine(f"sqlite:///{tmp_path}/topics.db")
    db.ensure_schema(engine)
    app.state.engine = engine
    issues.tools_app.state.engine = engine
    issues.tools_app.dependency_overrides[issues.get_reporter] = lambda: issues.Reporter(
        source="chatbot", email=None
    )
    monkeypatch.setattr(settings, "openmetadata_jwt", "jwt")

    async def fake_query(self: object, query: object) -> dict[str, Any]:
        return {"rows": ROWS, "row_count": 1, "truncated": False, "applied_limit": 1000}

    monkeypatch.setattr("ohdp_agent.cube.CubeClient.run_metric_query", fake_query)

    # Out of scope for this file (see test_dashboard_catalog.py) — neutralized
    # so these tests aren't exercising a real network call to OpenMetadata.
    async def fake_publish(self: object, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(
        "ohdp_agent.dashboard_catalog.DashboardCatalogClient.publish_dashboard", fake_publish
    )
    try:
        yield TestClient(app)
    finally:
        issues.tools_app.dependency_overrides.clear()
        app.state.engine = None
        issues.tools_app.state.engine = None


def test_a_save_files_the_dashboard_under_its_querys_topics(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Topics come from what the dashboard queries, not from a label the model
    picked — so the cube names are what reach the catalog."""
    asked: list[list[str]] = []

    async def fake_topics(self: object, table_names: list[str]) -> list[str]:
        asked.append(table_names)
        return ["Respiratory"]

    monkeypatch.setattr("ohdp_agent.domains.DomainsClient.topics_for_tables", fake_topics)

    saved = client.post("/tools/save_dashboard", json=SPEC).json()

    assert asked == [["respiratory__ed_visit_share_weekly"]]
    assert saved["topics"] == ["Respiratory"]
    assert client.get("/api/dashboards").json()[0]["topics"] == ["Respiratory"]


def test_an_unreachable_catalog_still_publishes_the_dashboard(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Topics are an enrichment. A catalog outage must not turn a save the user
    asked for into an error."""

    async def failing(self: object, table_names: list[str]) -> list[str]:
        raise DomainsError("OpenMetadata returned 503")

    monkeypatch.setattr("ohdp_agent.domains.DomainsClient.topics_for_tables", failing)

    saved = client.post("/tools/save_dashboard", json=SPEC)

    assert saved.status_code == 200
    assert saved.json()["topics"] == []


def test_no_catalog_configured_means_no_lookup_at_all(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "openmetadata_jwt", "")

    async def unexpected(self: object, table_names: list[str]) -> list[str]:
        raise AssertionError("the catalog should not be consulted")

    monkeypatch.setattr("ohdp_agent.domains.DomainsClient.topics_for_tables", unexpected)

    assert client.post("/tools/save_dashboard", json=SPEC).json()["topics"] == []


def test_every_cube_in_a_query_is_offered_to_the_catalog() -> None:
    """Filters and time dimensions name cubes too — a dashboard joining two
    should be findable under both their topics."""
    from ohdp_agent.models import CubeQuery

    query = CubeQuery.model_validate(
        {
            "measures": ["respiratory__ed_visit_share_weekly.avg_percent"],
            "dimensions": ["core__states.state_name"],
            "time_dimensions": [{"dimension": "respiratory__ed_visit_share_weekly.week_end"}],
            "filters": [
                {"member": "core__states.region", "operator": "equals", "values": ["South"]}
            ],
        }
    )

    assert library.cubes_in_query(query) == [
        "respiratory__ed_visit_share_weekly",
        "core__states",
    ]
