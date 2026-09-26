"""Publishing a saved dashboard to OpenMetadata.

Same shape of risk as the topic lookup (`test_dashboard_topics.py`): this is
the other part of saving that reaches outside the platform, and it must fail
quietly if it fails at all — a catalog outage is never a reason to refuse the
save the user asked for.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from hub_api import auth, chat, db, issues
from hub_api.main import app
from ohdp_agent.dashboard_catalog import CatalogWriteError, DashboardCatalogClient
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
        app.dependency_overrides.clear()
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


# --- deleting ---------------------------------------------------------------

AUTHOR = "author@example.org"


def _record_deletes(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    deleted: list[str] = []

    async def fake_delete(self: object, name: str) -> None:
        deleted.append(name)

    async def fake_publish(self: object, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(
        "ohdp_agent.dashboard_catalog.DashboardCatalogClient.delete_dashboard", fake_delete
    )
    monkeypatch.setattr(
        "ohdp_agent.dashboard_catalog.DashboardCatalogClient.publish_dashboard", fake_publish
    )
    return deleted


def test_an_author_deleting_their_dashboard_removes_it_from_the_catalog(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    deleted = _record_deletes(monkeypatch)
    app.dependency_overrides[chat.get_user_email] = lambda: AUTHOR
    client.post("/api/builder/publish", json={"spec_yaml": json.dumps(SPEC)})

    assert client.delete("/api/builder/published/ed-visits").status_code == 200
    assert deleted == ["ed-visits"]


def test_a_refused_delete_leaves_the_catalog_alone(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not the author's dashboard: the 404 comes back and nothing in the
    catalog is touched."""
    deleted = _record_deletes(monkeypatch)
    client.post("/tools/save_dashboard", json=SPEC)
    app.dependency_overrides[chat.get_user_email] = lambda: AUTHOR

    assert client.delete("/api/builder/published/ed-visits").status_code == 404
    assert deleted == []


def test_an_admin_delete_removes_it_from_the_catalog(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    deleted = _record_deletes(monkeypatch)
    client.post("/tools/save_dashboard", json=SPEC)
    app.dependency_overrides[auth.require_admin] = lambda: auth.User(email="admin@example.org")

    assert client.delete("/api/dashboards/ed-visits").status_code == 200
    assert deleted == ["ed-visits"]


def test_an_unreachable_catalog_does_not_undo_the_delete(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The row is gone before the catalog is asked, so a catalog outage is a
    stale entry there, not an error here."""
    _record_deletes(monkeypatch)

    async def failing(self: object, name: str) -> None:
        raise httpx.ConnectError("no route to OpenMetadata")

    monkeypatch.setattr(
        "ohdp_agent.dashboard_catalog.DashboardCatalogClient.delete_dashboard", failing
    )
    app.dependency_overrides[chat.get_user_email] = lambda: AUTHOR
    client.post("/api/builder/publish", json={"spec_yaml": json.dumps(SPEC)})

    assert client.delete("/api/builder/published/ed-visits").status_code == 200
    assert client.get("/api/builder/published").json() == []


async def test_the_catalog_delete_is_a_hard_delete_by_fqn() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={})

    catalog = DashboardCatalogClient(
        "https://om.example.org",
        "jwt",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    await catalog.delete_dashboard("ed-visits")

    [request] = requests
    assert request.method == "DELETE"
    assert request.url.path == "/api/v1/dashboards/name/ohdp-hub.ed-visits"
    assert request.url.params["hardDelete"] == "true"
    assert request.url.params["recursive"] == "true"


async def test_a_dashboard_the_catalog_never_had_is_already_deleted() -> None:
    """Saved while OpenMetadata was down or unconfigured: nothing to remove,
    and that is success, not an error."""
    catalog = DashboardCatalogClient(
        "https://om.example.org",
        "jwt",
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(404))),
    )
    await catalog.delete_dashboard("ed-visits")


async def test_any_other_catalog_refusal_is_a_write_error() -> None:
    catalog = DashboardCatalogClient(
        "https://om.example.org",
        "jwt",
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(500))),
    )
    with pytest.raises(CatalogWriteError):
        await catalog.delete_dashboard("ed-visits")
