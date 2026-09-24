"""Publishing a saved dashboard to OpenMetadata.

Same shape of risk as the topic lookup (`test_dashboard_topics.py`): this is
the other part of saving that reaches outside the platform, and it must fail
quietly if it fails at all — a catalog outage is never a reason to refuse the
save the user asked for.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hub_api import db, issues
from hub_api.main import app
from ohdp_agent.dashboard_catalog import CatalogWriteError
from ohdp_shared import settings

SPEC: dict[str, Any] = {
    "name": "ed-visits",
    "title": "ED visit share",
    "description": "Average share of ED visits, by week.",
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
    engine = db.make_engine(f"sqlite:///{tmp_path}/catalog.db")
    db.ensure_schema(engine)
    app.state.engine = engine
    issues.tools_app.state.engine = engine
    issues.tools_app.dependency_overrides[issues.get_reporter] = lambda: issues.Reporter(
        source="chatbot", email=None
    )
    monkeypatch.setattr(settings, "openmetadata_jwt", "jwt")
    monkeypatch.setattr(settings, "hub_base_url", "https://hub.example.org")

    async def fake_query(self: object, query: object) -> dict[str, Any]:
        return {"rows": ROWS, "row_count": 1, "truncated": False, "applied_limit": 1000}

    monkeypatch.setattr("ohdp_agent.cube.CubeClient.run_metric_query", fake_query)

    async def fake_topics(self: object, table_names: list[str]) -> list[str]:
        return ["Respiratory"]

    monkeypatch.setattr("ohdp_agent.domains.DomainsClient.topics_for_tables", fake_topics)
    try:
        yield TestClient(app)
    finally:
        issues.tools_app.dependency_overrides.clear()
        app.state.engine = None
        issues.tools_app.state.engine = None


def test_a_save_publishes_the_dashboard_with_its_url_topics_and_tables(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_publish(self: object, **kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(
        "ohdp_agent.dashboard_catalog.DashboardCatalogClient.publish_dashboard", fake_publish
    )

    saved = client.post("/tools/save_dashboard", json=SPEC).json()

    assert saved["topics"] == ["Respiratory"]
    assert calls == [
        {
            "name": "ed-visits",
            "title": "ED visit share",
            "description": "Average share of ED visits, by week.",
            "url": "https://hub.example.org/dashboards/ed-visits",
            "table_names": ["respiratory__ed_visit_share_weekly"],
            "domains": ["Respiratory"],
        }
    ]


def test_an_unreachable_catalog_still_saves_the_dashboard(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Publishing to OpenMetadata is an enrichment. A catalog outage must not
    turn a save the user asked for into an error."""

    async def failing(self: object, **kwargs: Any) -> None:
        raise CatalogWriteError("OpenMetadata returned 503")

    monkeypatch.setattr(
        "ohdp_agent.dashboard_catalog.DashboardCatalogClient.publish_dashboard", failing
    )

    saved = client.post("/tools/save_dashboard", json=SPEC)

    assert saved.status_code == 200
    assert saved.json()["name"] == "ed-visits"


def test_no_catalog_configured_means_no_publish_at_all(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "openmetadata_jwt", "")

    async def unexpected(self: object, **kwargs: Any) -> None:
        raise AssertionError("the catalog should not be published to")

    monkeypatch.setattr(
        "ohdp_agent.dashboard_catalog.DashboardCatalogClient.publish_dashboard", unexpected
    )

    assert client.post("/tools/save_dashboard", json=SPEC).status_code == 200
