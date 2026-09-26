"""The public half of the dashboard library: `/api/dashboards`, its data route,
and votes.

A dashboard the chat agent saved is published here, which is the boundary this
platform deliberately moved (`hub_api.library`'s docstring). These tests hold
the parts of that decision that are not self-evident from reading a route:

  - **No email reaches a public page.** `saved_by` is a signed-in user's
    address and this page has no wall in front of it.
  - **The page is rendered by us, from the spec, and served from storage.**
    The browser gets a document we produced out of a Cube query — never a
    chart the saver drew — under a CSP that keeps it off the hub's origin.
    What that trades away is freshness, so the age of the render is on every
    listing and a stale view schedules a new one.
  - **Votes are per person, and hiding follows from them.** One row per voter
    per dashboard, changeable and withdrawable, and `HIDE_AT_SCORE` is what
    drops a disliked dashboard off the page.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine

from hub_api import chat, dashboards, db, issues, library
from hub_api.main import app
from ohdp_shared import settings

SPEC: dict[str, Any] = {
    "name": "ed-visits",
    "title": "ED visit share",
    "description": "Average share of ED visits, by week.",
    "query": {
        "measures": ["ed_visits.avg_percent"],
        "time_dimensions": [{"dimension": "ed_visits.week_end", "granularity": "week"}],
    },
    "vega_lite": {
        "mark": "line",
        "encoding": {
            "x": {"field": "ed_visits.week_end", "type": "temporal"},
            "y": {"field": "ed_visits.avg_percent", "type": "quantitative"},
        },
    },
}

ROWS = [
    {"ed_visits.week_end.week": "2026-01-05", "ed_visits.avg_percent": 3.1},
    {"ed_visits.week_end.week": "2026-01-12", "ed_visits.avg_percent": 3.6},
]

VOTER = "voter@example.org"
OTHER_VOTER = "other@example.org"
# Who `/auth/login`'s local-dev bypass signs in as (hub_api.auth.login). The
# admin tests below use that bypass rather than a dependency override, so the
# email they name in `admin_emails` has to be this one.
DEV = "dev@localhost"


@pytest.fixture
def cube(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_query(self: object, query: object) -> dict[str, Any]:
        return {"rows": ROWS, "row_count": len(ROWS), "truncated": False, "applied_limit": 1000}

    monkeypatch.setattr("ohdp_agent.cube.CubeClient.run_metric_query", fake_query)


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    made: Engine = db.make_engine(f"sqlite:///{tmp_path}/public.db")
    db.ensure_schema(made)
    app.state.engine = made
    issues.tools_app.state.engine = made
    try:
        yield made
    finally:
        app.state.engine = None
        issues.tools_app.state.engine = None


@pytest.fixture
def client(engine: Engine) -> Iterator[TestClient]:
    """A caller that can save (the chat surface) but is signed out on the
    public routes — the default visitor."""
    issues.tools_app.dependency_overrides[issues.get_reporter] = lambda: issues.Reporter(
        source="chatbot", email=None
    )
    try:
        yield TestClient(app)
    finally:
        issues.tools_app.dependency_overrides.clear()


@pytest.fixture
def signed_in(client: TestClient) -> Iterator[TestClient]:
    app.dependency_overrides[chat.get_user_email] = lambda: VOTER
    app.dependency_overrides[chat.get_optional_user_email] = lambda: VOTER
    try:
        yield client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def browser(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """A caller holding a **real** signed-in session cookie, and no role.

    Deliberately not a dependency override like `signed_in` above. What the
    admin tests are checking is the chain from the session cookie through
    `auth.is_admin` to `auth.require_admin`, and overriding any link in it
    would leave the thing under test unexercised. `/auth/login` with no OIDC
    client configured is the local-dev bypass that mints exactly that cookie
    (`hub_api.auth.login`), so pointing the settings at local dev here gets a
    signed-in browser without an identity provider in the loop.
    """
    monkeypatch.setattr(settings, "environment", "local")
    monkeypatch.setattr(settings, "oidc_client_id", "")
    monkeypatch.setattr(settings, "admin_emails", "")
    assert client.get("/auth/login", follow_redirects=False).status_code == 303
    return client


@pytest.fixture
def admin(browser: TestClient, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """The same session, with that email named in the deployment's admin list."""
    monkeypatch.setattr(settings, "admin_emails", DEV)
    return browser


def _save(client: TestClient, **overrides: Any) -> None:
    response = client.post("/tools/save_dashboard", json={**SPEC, **overrides})
    assert response.status_code == 200, response.text


def _publish(engine: Engine, name: str, **values: Any) -> None:
    """Write a row directly, for the cases a save cannot reach (votes already
    cast, a curated row, a page rendered hours ago)."""
    now = datetime.now(UTC)
    defaults = {
        "name": name,
        "title": name,
        "description": "",
        "spec": {**SPEC, "name": name},
        "html": f"<!DOCTYPE html><html><body>{name}</body></html>",
        "last_rendered": now,
        "source": "curated",
        "topics": [],
        "featured": False,
        "upvotes": 0,
        "downvotes": 0,
        "created_at": now,
        "updated_at": now,
    }
    with engine.begin() as connection:
        connection.execute(library.dashboards.insert().values({**defaults, **values}))


# --- listing ---------------------------------------------------------------


def test_a_saved_dashboard_is_published_on_the_page(client: TestClient, cube: None) -> None:
    """The change this whole feature is: what the agent saves, visitors see."""
    _save(client)

    listed = client.get("/api/dashboards").json()

    assert [entry["name"] for entry in listed] == ["ed-visits"]
    assert listed[0]["source"] == "chat"
    assert listed[0]["query"] == SPEC["query"]


def test_a_public_listing_never_carries_the_email_that_saved_it(
    client: TestClient, cube: None, engine: Engine
) -> None:
    """`saved_by` is a real address and this page is unauthenticated."""
    _publish(engine, "by-a-person", saved_by="researcher@example.org", source="chat")

    body = client.get("/api/dashboards").text

    assert "researcher@example.org" not in body
    assert "saved_by" not in body


def test_the_listing_carries_no_chart_only_its_query(client: TestClient, cube: None) -> None:
    """A list of unbound specs would be kilobytes that cannot be drawn anyway —
    the chart comes from the data route, one dashboard at a time."""
    _save(client)

    entry = client.get("/api/dashboards").json()[0]

    assert "vega_lite" not in entry
    assert "spec" not in entry


def test_the_listing_names_its_chart_types(client: TestClient, cube: None) -> None:
    """The page filters by mark type, which is all of the spec a listing carries."""
    _save(client)

    assert client.get("/api/dashboards").json()[0]["chart_types"] == ["line"]


def test_chart_types_walk_composed_specs() -> None:
    spec = {
        "layer": [
            {"mark": {"type": "line", "point": True}},
            {"mark": "rule"},
            {"hconcat": [{"mark": "bar"}, {"facet": {}, "spec": {"mark": "line"}}]},
        ]
    }

    assert library.chart_types(spec) == ["line", "rule", "bar"]
    assert library.chart_types(None) == []


def test_scatter_marks_fold_into_one_chart_type() -> None:
    assert library.chart_types({"layer": [{"mark": "circle"}, {"mark": "point"}]}) == ["point"]


def test_rows_seeded_with_the_old_vega_key_still_name_their_type(
    client: TestClient, engine: Engine
) -> None:
    _publish(engine, "legacy", spec={"name": "legacy", "query": {}, "vega": {"mark": "bar"}})

    assert client.get("/api/dashboards").json()[0]["chart_types"] == ["bar"]


def _bar(name: str) -> dict[str, Any]:
    return {**SPEC, "name": name, "vega_lite": {**SPEC["vega_lite"], "mark": "bar"}}


def test_the_dashboards_page_is_paged_in_listing_order(client: TestClient, engine: Engine) -> None:
    for score in range(5):
        _publish(engine, f"d{score}", upvotes=score)

    page = client.get("/api/dashboards/page", params={"limit": 2, "offset": 2}).json()

    assert [entry["name"] for entry in page["dashboards"]] == ["d2", "d1"]
    assert page["total"] == 5


def test_the_page_filters_by_chart_type_and_counts_every_type(
    client: TestClient, engine: Engine
) -> None:
    """The counts ignore the chosen type — they are what picking each type would show."""
    _publish(engine, "flu-line")
    _publish(engine, "flu-bar", spec=_bar("flu-bar"))
    _publish(engine, "aqi-bar", spec=_bar("aqi-bar"))

    page = client.get("/api/dashboards/page", params={"chart_type": "line"}).json()

    assert [entry["name"] for entry in page["dashboards"]] == ["flu-line"]
    assert page["total"] == 1
    assert page["search_total"] == 3
    assert page["chart_types"] == {"line": 1, "bar": 2}


def test_the_page_searches_titles_and_topics(client: TestClient, engine: Engine) -> None:
    _publish(engine, "flu-bar", title="Flu visits", spec=_bar("flu-bar"))
    _publish(engine, "aqi-line", title="Air quality", topics=["Environment"])
    _publish(engine, "aqi-bar", title="AQI by state", topics=["Environment"], spec=_bar("aqi-bar"))

    page = client.get("/api/dashboards/page", params={"q": "environment"}).json()

    assert {entry["name"] for entry in page["dashboards"]} == {"aqi-line", "aqi-bar"}
    assert page["chart_types"] == {"line": 1, "bar": 1}


def test_the_page_route_is_not_read_as_a_dashboard_name(client: TestClient) -> None:
    response = client.get("/api/dashboards/page")

    assert response.status_code == 200
    assert response.json() == {"dashboards": [], "total": 0, "search_total": 0, "chart_types": {}}


def test_dashboards_are_filtered_by_topic(client: TestClient, engine: Engine) -> None:
    _publish(engine, "respiratory-one", topics=["Respiratory"])
    _publish(engine, "chronic-one", topics=["Chronic Disease"])

    listed = client.get("/api/dashboards", params={"topic": "Respiratory"}).json()

    assert [entry["name"] for entry in listed] == ["respiratory-one"]


def test_featured_then_score_then_recency(client: TestClient, engine: Engine) -> None:
    """Our own picks stay on top; the crowd sorts the rest."""
    _publish(engine, "liked", upvotes=5)
    _publish(engine, "ignored")
    _publish(engine, "ours", featured=True)

    listed = [entry["name"] for entry in client.get("/api/dashboards").json()]

    assert listed == ["ours", "liked", "ignored"]


def test_no_database_degrades_to_an_empty_list(client: TestClient) -> None:
    """A public marketing surface must not 503 because Postgres is restarting."""
    app.state.engine = None

    response = client.get("/api/dashboards")

    assert response.status_code == 200
    assert response.json() == []


# --- the chart -------------------------------------------------------------


def test_the_page_is_the_one_rendered_at_save_time(client: TestClient, cube: None) -> None:
    """Saving renders to validate; that same page is what visitors get, rather
    than a second render on first view."""
    _save(client)

    response = client.get("/api/dashboards/ed-visits/html")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "vegaEmbed" in response.text
    # The rows are in the page, bound server-side — the browser never queries
    # Cube and the spec never carried them.
    assert "3.1" in response.text


def test_the_page_is_served_sandboxed(client: TestClient, cube: None) -> None:
    """This document is model-influenced and is built to live in a sandboxed
    iframe. Anyone can also open the URL directly, and without this header that
    would be a top-level document on the hub's own origin."""
    _save(client)

    headers = client.get("/api/dashboards/ed-visits/html").headers

    assert headers["content-security-policy"] == "sandbox allow-scripts allow-popups"
    assert "allow-same-origin" not in headers["content-security-policy"]
    assert headers["x-content-type-options"] == "nosniff"


def test_a_fresh_page_schedules_nothing(
    client: TestClient, cube: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The common case must not touch the warehouse at all."""
    _save(client)
    refreshed: list[str] = []

    async def record(engine: Engine, name: str, spec: dict[str, Any]) -> None:
        refreshed.append(name)

    monkeypatch.setattr(dashboards, "refresh_render", record)

    client.get("/api/dashboards/ed-visits/html")

    assert refreshed == []


def test_a_stale_page_is_served_now_and_refreshed_after(
    client: TestClient, engine: Engine, cube: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reader who finds it stale is not the one who waits for Cube: the old
    page comes back, and the re-render runs behind the response."""
    old = datetime.now(UTC) - timedelta(seconds=library.STALE_AFTER_SECONDS + 60)
    _publish(engine, "aging", last_rendered=old)
    refreshed: list[str] = []

    async def record(engine_: Engine, name: str, spec: dict[str, Any]) -> None:
        refreshed.append(name)

    monkeypatch.setattr(dashboards, "refresh_render", record)

    response = client.get("/api/dashboards/aging/html")

    assert "aging" in response.text
    assert refreshed == ["aging"]


def test_a_refresh_replaces_the_page_without_reordering_the_library(
    client: TestClient, engine: Engine, cube: None
) -> None:
    """A re-render changes the numbers, not the dashboard — so `updated_at`
    (what the listing sorts on) must not move with it."""
    _save(client)
    before = library.by_name(engine, "ed-visits")
    assert before is not None

    library.store_render(engine, name="ed-visits", html="<html>fresher</html>")

    after = library.by_name(engine, "ed-visits")
    assert after is not None
    assert after["updated_at"] == before["updated_at"]
    assert after["last_rendered"] > before["last_rendered"]
    assert client.get("/api/dashboards/ed-visits/html").text == "<html>fresher</html>"


def test_the_scheduled_refresh_actually_runs_and_replaces_the_page(
    client: TestClient, engine: Engine, cube: None
) -> None:
    """Not monkeypatched: the whole path, from a stale view to a stored page
    with fresh rows in it. A background task that silently never runs would
    leave every dashboard frozen at its save-time numbers."""
    _save(client)
    library.store_render(engine, name="ed-visits", html="<html>stale</html>")
    with engine.begin() as connection:
        connection.execute(
            library.dashboards.update()
            .where(library.dashboards.c.name == "ed-visits")
            .values(
                last_rendered=datetime.now(UTC)
                - timedelta(seconds=library.STALE_AFTER_SECONDS + 60)
            )
        )

    served = client.get("/api/dashboards/ed-visits/html")

    # The stale page is what this visitor got...
    assert served.text == "<html>stale</html>"
    # ...and the next one gets the re-render the view scheduled.
    assert "vegaEmbed" in client.get("/api/dashboards/ed-visits/html").text
    listed = client.get("/api/dashboards").json()[0]
    assert listed["stale"] is False


def test_the_listing_says_how_old_the_numbers_are(
    client: TestClient, engine: Engine, cube: None
) -> None:
    """A stored render is a picture and can go stale; hiding that from a reader
    is the one thing this design must not do."""
    _save(client)
    old = datetime.now(UTC) - timedelta(seconds=library.STALE_AFTER_SECONDS + 60)
    _publish(engine, "aging", last_rendered=old)

    listed = {entry["name"]: entry for entry in client.get("/api/dashboards").json()}

    assert listed["ed-visits"]["stale"] is False
    assert listed["aging"]["stale"] is True
    assert listed["aging"]["last_rendered"] is not None


def test_a_listing_does_not_carry_the_rendered_pages(client: TestClient, cube: None) -> None:
    """Twenty rendered dashboards is megabytes of HTML; the page loads them one
    iframe at a time instead."""
    _save(client)

    entry = client.get("/api/dashboards").json()[0]

    assert "html" not in entry


def test_an_unknown_dashboard_has_no_page(client: TestClient) -> None:
    assert client.get("/api/dashboards/nope/html").status_code == 404


# --- one dashboard's own page ------------------------------------------------


def test_a_saved_dashboard_has_its_own_metadata_route(client: TestClient, cube: None) -> None:
    """The single-dashboard page's read: the same shape a listing entry has,
    for exactly the one name asked for."""
    _save(client)

    entry = client.get("/api/dashboards/ed-visits").json()

    assert entry["name"] == "ed-visits"
    assert entry["title"] == SPEC["title"]
    assert entry["query"] == SPEC["query"]
    assert "vega_lite" not in entry
    assert "spec" not in entry
    assert "saved_by" not in entry


def test_an_unknown_dashboards_metadata_route_is_404(client: TestClient) -> None:
    assert client.get("/api/dashboards/nope").status_code == 404


def test_a_hidden_dashboard_still_has_a_metadata_route(
    client: TestClient, engine: Engine, cube: None
) -> None:
    """Hiding is a display rule on the listing, not a delete — a direct link
    to a downvoted dashboard should keep working."""
    _save(client)
    for voter in ("a@x.test", "b@x.test"):
        library.vote(engine, name="ed-visits", voter_email=voter, value=-1)

    entry = client.get("/api/dashboards/ed-visits").json()

    assert entry["hidden"] is True
    assert client.get("/api/dashboards").json() == []


def test_the_metadata_route_shows_this_viewers_own_vote(signed_in: TestClient, cube: None) -> None:
    _save(signed_in)
    signed_in.post("/api/dashboards/ed-visits/vote", json={"value": 1})

    entry = signed_in.get("/api/dashboards/ed-visits").json()

    assert entry["my_vote"] == 1


# --- votes -----------------------------------------------------------------


def test_voting_requires_signing_in(client: TestClient, cube: None) -> None:
    """One row per person per dashboard only means something if "person" is
    verified — otherwise the ranking is writable by anyone who can POST."""
    _save(client)

    assert client.post("/api/dashboards/ed-visits/vote", json={"value": 1}).status_code == 401


def test_a_vote_moves_the_score_and_comes_back_on_the_listing(
    signed_in: TestClient, cube: None
) -> None:
    _save(signed_in)

    voted = signed_in.post("/api/dashboards/ed-visits/vote", json={"value": 1}).json()

    assert voted["score"] == 1
    assert voted["my_vote"] == 1
    listed = signed_in.get("/api/dashboards").json()[0]
    assert listed["score"] == 1
    assert listed["my_vote"] == 1


def test_voting_twice_replaces_rather_than_stacks(signed_in: TestClient, cube: None) -> None:
    """The counters are recomputed from the votes table, so flipping a vote
    cannot leave the two disagreeing."""
    _save(signed_in)

    signed_in.post("/api/dashboards/ed-visits/vote", json={"value": 1})
    flipped = signed_in.post("/api/dashboards/ed-visits/vote", json={"value": -1}).json()

    assert (flipped["upvotes"], flipped["downvotes"], flipped["score"]) == (0, 1, -1)


def test_a_vote_can_be_withdrawn(signed_in: TestClient, cube: None) -> None:
    _save(signed_in)
    signed_in.post("/api/dashboards/ed-visits/vote", json={"value": 1})

    withdrawn = signed_in.post("/api/dashboards/ed-visits/vote", json={"value": 0}).json()

    assert withdrawn["score"] == 0
    assert withdrawn["my_vote"] == 0


def test_another_viewers_vote_is_not_shown_as_yours(
    client: TestClient, engine: Engine, cube: None
) -> None:
    """`my_vote` is per viewer; a signed-out visitor has none at all."""
    _save(client)
    library.vote(engine, name="ed-visits", voter_email=OTHER_VOTER, value=1)

    anonymous = client.get("/api/dashboards").json()[0]

    assert anonymous["score"] == 1
    assert anonymous["my_vote"] == 0


def test_a_disliked_dashboard_drops_off_the_public_page(
    client: TestClient, engine: Engine, cube: None
) -> None:
    """The mechanism the feature rests on: nobody deletes a bad dashboard, it
    just stops being shown once people vote it down."""
    _save(client)
    for voter in ("a@x.test", "b@x.test"):
        library.vote(engine, name="ed-visits", voter_email=voter, value=-1)

    assert client.get("/api/dashboards").json() == []


def test_a_hidden_dashboard_is_still_readable_by_the_agent(
    client: TestClient, engine: Engine, cube: None
) -> None:
    """Hiding is a display rule, not a delete: the agent has to be able to see
    what was voted down in order to fix it."""
    _save(client)
    for voter in ("a@x.test", "b@x.test"):
        library.vote(engine, name="ed-visits", voter_email=voter, value=-1)

    entry = client.get("/tools/get_dashboard", params={"name": "ed-visits"}).json()["dashboards"][0]

    assert entry["hidden"] is True
    assert entry["score"] == -2


def test_voting_on_a_dashboard_that_does_not_exist(signed_in: TestClient) -> None:
    assert signed_in.post("/api/dashboards/nope/vote", json={"value": 1}).status_code == 404


def test_a_vote_must_be_up_down_or_withdrawn(signed_in: TestClient, cube: None) -> None:
    """No weighted votes: the value is a closed set, checked before it reaches
    the database."""
    _save(signed_in)

    assert signed_in.post("/api/dashboards/ed-visits/vote", json={"value": 7}).status_code == 422


# --- deleting (admins only) ------------------------------------------------


def test_an_admin_can_take_a_dashboard_down(admin: TestClient, cube: None) -> None:
    """The escape hatch voting does not provide: a dashboard that should never
    have been published is gone now, not once two people have downvoted it."""
    _save(admin)

    response = admin.delete("/api/dashboards/ed-visits")

    assert response.status_code == 200
    assert response.json()["deleted"]["title"] == SPEC["title"]
    assert admin.get("/api/dashboards").json() == []


def test_a_delete_takes_the_render_and_the_votes_with_it(
    admin: TestClient, engine: Engine, cube: None
) -> None:
    """Nothing is left behind to resurrect or to count. The votes matter
    because the foreign key's CASCADE is not honoured by SQLite without a
    pragma we do not set — `library.delete` deletes them itself."""
    _save(admin)
    library.vote(engine, name="ed-visits", voter_email=OTHER_VOTER, value=1)

    admin.delete("/api/dashboards/ed-visits")

    assert library.by_name(engine, "ed-visits") is None
    assert library.rendered_page(engine, "ed-visits") is None
    with engine.connect() as connection:
        assert connection.execute(select(library.dashboard_votes)).first() is None


def test_a_signed_in_visitor_who_is_not_an_admin_cannot_delete(
    browser: TestClient, cube: None
) -> None:
    """Voting is what a signed-in visitor gets. Deleting is not — and the
    dashboard is still there afterwards, which is the part that matters."""
    _save(browser)

    response = browser.delete("/api/dashboards/ed-visits")

    assert response.status_code == 403
    assert [entry["name"] for entry in browser.get("/api/dashboards").json()] == ["ed-visits"]


def test_a_signed_out_visitor_cannot_delete(client: TestClient, cube: None) -> None:
    _save(client)

    assert client.delete("/api/dashboards/ed-visits").status_code == 401


def test_the_role_is_read_per_request_not_frozen_into_the_session(
    admin: TestClient, monkeypatch: pytest.MonkeyPatch, cube: None
) -> None:
    """The reason `is_admin` reads configuration instead of the session it was
    issued with: a session cookie lives fourteen days, and revoking an admin
    must not have to wait that long. Same cookie, admin removed, refused."""
    _save(admin)
    monkeypatch.setattr(settings, "admin_emails", "someone-else@example.org")

    assert admin.delete("/api/dashboards/ed-visits").status_code == 403


def test_the_admin_list_is_case_insensitive(
    browser: TestClient, monkeypatch: pytest.MonkeyPatch, cube: None
) -> None:
    """An IdP's casing is not a permission boundary."""
    _save(browser)
    monkeypatch.setattr(settings, "admin_emails", DEV.upper())

    assert browser.delete("/api/dashboards/ed-visits").status_code == 200


def test_deleting_a_dashboard_that_does_not_exist(admin: TestClient) -> None:
    assert admin.delete("/api/dashboards/nope").status_code == 404


def test_the_session_route_reports_the_role(admin: TestClient) -> None:
    """What the UI decides whether to offer a delete button on."""
    assert admin.get("/auth/me").json()["is_admin"] is True


def test_the_session_route_reports_no_role_for_everyone_else(browser: TestClient) -> None:
    assert browser.get("/auth/me").json()["is_admin"] is False
