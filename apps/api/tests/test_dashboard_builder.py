"""The builder: a signed-in person writing a `DashboardSpec` by hand.

What these tests hold is the part of `hub_api.builder` that is a decision
rather than plumbing:

  - **A person authors a spec, never a page.** Preview and publish both take
    YAML, parse it into the same `DashboardSpec` the agent's tools take, and
    render it here — so the validation that stops a model inventing numbers
    (no literal `data` values, a `CubeQuery` Cube has to accept) applies
    unchanged to a human, and no HTML a visitor wrote reaches anyone.
  - **A draft is private text.** It does not have to parse, it is scoped to its
    author by the query rather than by a check, and another author's id is
    indistinguishable from one that does not exist.
  - **Publishing is publishing, and a name has an owner.** A published
    dashboard is on the public page immediately, and the flat name namespace
    cannot be used to take over — or be taken over by — someone else's chart,
    including the chat agent's.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

from hub_api import builder, chat, db, drafts, issues, library
from hub_api.main import app
from ohdp_agent.cube import CubeError, CubeInfo, DimensionInfo, MeasureInfo

AUTHOR = "author@example.org"
OTHER = "other@example.org"

SPEC: dict[str, Any] = {
    "name": "my-ed-visits",
    "title": "ED visit share",
    "description": "Average share of ED visits, by week.",
    "query": {
        "measures": ["ed_visits.avg_percent"],
        "time_dimensions": [{"dimension": "ed_visits.week_end", "granularity": "week"}],
    },
    "vega": {
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

CUBES = (
    CubeInfo(
        name="ed_visits",
        title="ED visits",
        description="Emergency department visits.",
        measures=(
            MeasureInfo(
                name="ed_visits.avg_percent",
                title="Average percent",
                description="Mean share of visits.",
                cube="ed_visits",
                agg_type="avg",
            ),
        ),
        dimensions=(
            DimensionInfo(
                name="ed_visits.week_end",
                title="Week ending",
                description="",
                cube="ed_visits",
                type="time",
            ),
        ),
    ),
)


def _yaml(**overrides: Any) -> str:
    return yaml.safe_dump({**SPEC, **overrides}, sort_keys=False)


@pytest.fixture
def cube(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_query(self: object, query: object) -> dict[str, Any]:
        return {"rows": ROWS, "row_count": len(ROWS), "truncated": False, "applied_limit": 1000}

    async def fake_meta(self: object) -> tuple[CubeInfo, ...]:
        return CUBES

    monkeypatch.setattr("ohdp_agent.cube.CubeClient.run_metric_query", fake_query)
    monkeypatch.setattr("ohdp_agent.cube.CubeClient.list_metrics", fake_meta)
    # The `/meta` cache is process-global, so one test's answer must not be
    # another's (`hub_api.builder._cubes_cache`).
    monkeypatch.setattr(builder, "_cubes_cache", None)


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    made: Engine = db.make_engine(f"sqlite:///{tmp_path}/builder.db")
    db.ensure_schema(made)
    app.state.engine = made
    issues.tools_app.state.engine = made
    try:
        yield made
    finally:
        app.state.engine = None
        issues.tools_app.state.engine = None


@pytest.fixture
def anon(engine: Engine) -> Iterator[TestClient]:
    """A signed-out visitor, and the chat surface on the `/tools` mount — the
    other publisher, for the ownership tests."""
    issues.tools_app.dependency_overrides[issues.get_reporter] = lambda: issues.Reporter(
        source="chatbot", email=None
    )
    try:
        yield TestClient(app)
    finally:
        issues.tools_app.dependency_overrides.clear()


@pytest.fixture
def author(anon: TestClient) -> Iterator[TestClient]:
    app.dependency_overrides[chat.get_user_email] = lambda: AUTHOR
    app.dependency_overrides[chat.get_optional_user_email] = lambda: AUTHOR
    try:
        yield anon
    finally:
        app.dependency_overrides.clear()


def _as(client: TestClient, email: str) -> TestClient:
    """Switch which signed-in identity the hub's routes see."""
    app.dependency_overrides[chat.get_user_email] = lambda: email
    app.dependency_overrides[chat.get_optional_user_email] = lambda: email
    return client


# --- preview ---------------------------------------------------------------


def test_preview_draws_the_spec_and_saves_nothing(author: TestClient, cube: None) -> None:
    """The live render: a page comes back, the library does not grow."""
    response = author.post("/api/builder/preview", json={"spec_yaml": _yaml()})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "my-ed-visits"
    assert body["row_count"] == 2
    assert body["html"].startswith("<!DOCTYPE html>")
    # The rows are in the page because we bound them, not because the author
    # sent them.
    assert "3.6" in body["html"]
    assert author.get("/api/dashboards").json() == []


def test_preview_rejects_yaml_that_is_not_yaml(author: TestClient, cube: None) -> None:
    response = author.post("/api/builder/preview", json={"spec_yaml": "vega: [unclosed"})

    assert response.status_code == 422
    assert "not valid YAML" in response.json()["detail"]


def test_preview_names_the_field_a_bad_spec_got_wrong(author: TestClient, cube: None) -> None:
    """A valid document that is not a `DashboardSpec` is a modelling mistake,
    and the field path is the only thing that says which key to fix."""
    broken = yaml.safe_dump({**SPEC, "name": "Not Kebab Case"})

    response = author.post("/api/builder/preview", json={"spec_yaml": broken})

    assert response.status_code == 422
    assert "name" in response.json()["detail"]


def test_preview_refuses_a_spec_carrying_its_own_numbers(author: TestClient, cube: None) -> None:
    """The property the whole dashboard path exists to hold, applied to a human:
    rows come from Cube or they do not come at all."""
    smuggled = _yaml(
        vega={
            "data": {"values": [{"x": 1, "y": 2}]},
            "mark": "line",
            "encoding": {"x": {"field": "x"}, "y": {"field": "y"}},
        }
    )

    response = author.post("/api/builder/preview", json={"spec_yaml": smuggled})

    assert response.status_code == 422
    assert "data" in response.json()["detail"]


def test_preview_surfaces_what_the_semantic_layer_said(
    author: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cube's own message names the wrong member, which is what the author
    needs to fix the spec."""

    async def rejects(self: object, query: object) -> dict[str, Any]:
        raise CubeError("Cube returned 400: no such member ed_visits.nope")

    monkeypatch.setattr("ohdp_agent.cube.CubeClient.run_metric_query", rejects)

    response = author.post("/api/builder/preview", json={"spec_yaml": _yaml()})

    assert response.status_code == 400
    assert "ed_visits.nope" in response.json()["detail"]


def test_the_builder_is_behind_the_sign_in_wall(anon: TestClient, cube: None) -> None:
    """Every route, including the read-only ones: preview runs a Cube query the
    caller composed, and the reference panel is a signed-in surface too."""
    assert anon.post("/api/builder/preview", json={"spec_yaml": _yaml()}).status_code == 401
    assert anon.get("/api/builder/drafts").status_code == 401
    assert anon.post("/api/builder/drafts", json={"spec_yaml": "name: x"}).status_code == 401
    assert anon.post("/api/builder/publish", json={"spec_yaml": _yaml()}).status_code == 401
    assert anon.get("/api/builder/published").status_code == 401
    assert anon.get("/api/semantic/cubes").status_code == 401


# --- drafts ----------------------------------------------------------------


def test_a_draft_need_not_parse(author: TestClient) -> None:
    """The normal state of a dashboard halfway through being written. A draft
    that must already be valid would not be a draft."""
    response = author.post("/api/builder/drafts", json={"spec_yaml": "title: half a th"})

    assert response.status_code == 200, response.text
    draft = response.json()
    assert draft["spec_yaml"] == "title: half a th"
    assert draft["name"] == ""


def test_a_draft_keeps_the_text_exactly_as_typed(author: TestClient) -> None:
    """Comments and formatting survive, which is the point of storing YAML text
    rather than a re-dumped spec."""
    typed = "# my chart\nname: my-ed-visits\ntitle: ED visit share\n"

    author.post("/api/builder/drafts", json={"spec_yaml": typed})

    assert author.get("/api/builder/drafts").json()[0]["spec_yaml"] == typed


def test_a_draft_is_labelled_by_the_spec_inside_it(author: TestClient) -> None:
    draft = author.post("/api/builder/drafts", json={"spec_yaml": _yaml()}).json()

    assert (draft["name"], draft["title"]) == ("my-ed-visits", "ED visit share")


def test_editing_a_draft_replaces_its_text(author: TestClient) -> None:
    draft = author.post("/api/builder/drafts", json={"spec_yaml": "name: a-start"}).json()

    updated = author.put(
        f"/api/builder/drafts/{draft['id']}", json={"spec_yaml": "name: a-finish"}
    ).json()

    assert updated["spec_yaml"] == "name: a-finish"
    assert [row["spec_yaml"] for row in author.get("/api/builder/drafts").json()] == [
        "name: a-finish"
    ]


def test_another_authors_draft_does_not_exist(author: TestClient) -> None:
    """Scoped by the query, so "someone else's" and "no such draft" are the
    same answer — there is nothing to learn from the status code."""
    mine = author.post("/api/builder/drafts", json={"spec_yaml": "name: mine"}).json()

    intruder = _as(author, OTHER)

    assert intruder.get("/api/builder/drafts").json() == []
    assert (
        intruder.put(
            f"/api/builder/drafts/{mine['id']}", json={"spec_yaml": "name: theirs"}
        ).status_code
        == 404
    )
    assert intruder.delete(f"/api/builder/drafts/{mine['id']}").status_code == 404
    # And it is untouched.
    assert _as(author, AUTHOR).get("/api/builder/drafts").json()[0]["spec_yaml"] == "name: mine"


def test_deleting_a_draft_leaves_the_published_dashboard(author: TestClient, cube: None) -> None:
    """Publishing copies the spec into the library, so a draft is not a
    dependency of the dashboard it became."""
    draft = author.post("/api/builder/drafts", json={"spec_yaml": _yaml()}).json()
    assert author.post("/api/builder/publish", json={"spec_yaml": _yaml()}).status_code == 200

    assert author.delete(f"/api/builder/drafts/{draft['id']}").json() == {"ok": True}

    assert [d["name"] for d in author.get("/api/dashboards").json()] == ["my-ed-visits"]


def test_drafts_stop_at_the_per_author_ceiling(
    author: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(drafts, "MAX_DRAFTS_PER_AUTHOR", 2)

    for index in range(2):
        assert (
            author.post("/api/builder/drafts", json={"spec_yaml": f"name: d{index}"}).status_code
            == 200
        )
    response = author.post("/api/builder/drafts", json={"spec_yaml": "name: one-too-many"})

    assert response.status_code == 409
    assert "limit" in response.json()["detail"]


# --- publishing ------------------------------------------------------------


def test_publishing_puts_it_on_the_public_page(author: TestClient, cube: None) -> None:
    response = author.post("/api/builder/publish", json={"spec_yaml": _yaml()})

    assert response.status_code == 200, response.text
    assert response.json()["created"] is True
    assert response.json()["url"] == "/dashboards/my-ed-visits"

    listed = author.get("/api/dashboards").json()
    assert [entry["name"] for entry in listed] == ["my-ed-visits"]
    # `source` says a person wrote this one, without naming them.
    assert listed[0]["source"] == "user"
    assert "author@example.org" not in author.get("/api/dashboards").text


def test_a_published_dashboard_is_served_as_a_page_we_rendered(
    author: TestClient, cube: None
) -> None:
    """The render made at publish time is what visitors get, under the sandbox
    header — the same contract as an agent-saved dashboard."""
    author.post("/api/builder/publish", json={"spec_yaml": _yaml()})

    page = author.get("/api/dashboards/my-ed-visits/html")

    assert page.status_code == 200
    assert "sandbox" in page.headers["content-security-policy"]
    assert "3.6" in page.text


def test_republishing_the_same_name_is_a_correction(author: TestClient, cube: None) -> None:
    """Same name, same chart: the row is replaced and its votes survive, which
    is why a fix does not cost a dashboard its standing."""
    author.post("/api/builder/publish", json={"spec_yaml": _yaml()})
    author.post("/api/dashboards/my-ed-visits/vote", json={"value": 1})

    again = author.post("/api/builder/publish", json={"spec_yaml": _yaml(title="ED visits, fixed")})

    assert again.json()["created"] is False
    entry = author.get("/api/dashboards/my-ed-visits").json()
    assert entry["title"] == "ED visits, fixed"
    assert entry["score"] == 1


def test_a_name_someone_else_published_is_refused(author: TestClient, cube: None) -> None:
    """The flat namespace must not be a way to take over another person's
    dashboard — along with the votes it earned."""
    author.post("/api/builder/publish", json={"spec_yaml": _yaml()})

    response = _as(author, OTHER).post(
        "/api/builder/publish", json={"spec_yaml": _yaml(title="mine now")}
    )

    assert response.status_code == 409
    assert "not yours" in response.json()["detail"]
    assert _as(author, AUTHOR).get("/api/dashboards/my-ed-visits").json()["title"] == (
        "ED visit share"
    )


def test_a_name_the_agent_published_is_refused(author: TestClient, cube: None) -> None:
    """Not because the agent's work outranks a person's — because overwriting
    it silently is not how either of them corrects a dashboard."""
    assert (
        author.post("/tools/save_dashboard", json={**SPEC, "name": "agent-chart"}).status_code
        == 200
    )

    response = author.post("/api/builder/publish", json={"spec_yaml": _yaml(name="agent-chart")})

    assert response.status_code == 409
    assert author.get("/api/dashboards/agent-chart").json()["source"] == "chat"


def test_the_agent_cannot_save_over_a_persons_dashboard(author: TestClient, cube: None) -> None:
    """The same rule from the other side. `save_dashboard` overwrites by name,
    which was harmless while nobody but us and the agent published."""
    author.post("/api/builder/publish", json={"spec_yaml": _yaml()})

    response = author.post("/tools/save_dashboard", json={**SPEC, "title": "the agent's version"})

    assert response.status_code == 409
    assert "different name" in response.json()["detail"]
    assert author.get("/api/dashboards/my-ed-visits").json()["title"] == "ED visit share"


def test_a_dashboard_saved_in_a_signed_in_chat_is_still_the_authors(
    author: TestClient, cube: None
) -> None:
    """Ownership is the address, not the channel.

    `save_dashboard` reached by a browser session records that person as
    `saved_by` while marking the row `source="chat"`. Keying the rule on
    `source` would have made their own dashboard unrepublishable from the
    builder — the one place they can edit its spec.
    """
    issues.tools_app.dependency_overrides[issues.get_reporter] = lambda: issues.Reporter(
        source="web", email=AUTHOR
    )
    assert author.post("/tools/save_dashboard", json=SPEC).status_code == 200

    response = author.post("/api/builder/publish", json={"spec_yaml": _yaml(title="now mine")})

    assert response.status_code == 200, response.text
    assert author.get("/api/dashboards/my-ed-visits").json()["title"] == "now mine"


def test_a_curated_dashboards_name_is_not_free_to_take(
    author: TestClient, cube: None, engine: Engine
) -> None:
    """A row with no owner is ours, not unclaimed: republishing over it would
    take a curated dashboard's name and the votes it earned."""
    now = datetime.now(UTC)
    with engine.begin() as connection:
        connection.execute(
            library.dashboards.insert().values(
                name="my-ed-visits",
                title="Ours",
                description="",
                spec=SPEC,
                html="<!DOCTYPE html><html><body>ours</body></html>",
                last_rendered=now,
                source="curated",
                saved_by=None,
                topics=[],
                featured=False,
                upvotes=0,
                downvotes=0,
                created_at=now,
                updated_at=now,
            )
        )

    response = author.post("/api/builder/publish", json={"spec_yaml": _yaml()})

    assert response.status_code == 409
    assert author.get("/api/dashboards/my-ed-visits").json()["title"] == "Ours"


def test_a_spec_that_cannot_be_drawn_is_never_published(author: TestClient, cube: None) -> None:
    """Validated by doing, like `save_dashboard`: the chart is drawn before the
    row is written, so the library cannot fill with dashboards that fail the
    first time someone opens them."""
    response = author.post(
        "/api/builder/publish",
        json={
            "spec_yaml": _yaml(
                vega={
                    "mark": "line",
                    "encoding": {"x": {"field": "ed_visits.nonexistent", "type": "temporal"}},
                }
            )
        },
    )

    assert response.status_code == 422
    assert author.get("/api/dashboards").json() == []


def test_publishing_stops_at_the_library_ceiling(
    author: TestClient, cube: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(library, "MAX_DASHBOARDS", 1)
    author.post("/api/builder/publish", json={"spec_yaml": _yaml()})

    response = author.post("/api/builder/publish", json={"spec_yaml": _yaml(name="one-more")})

    assert response.status_code == 409
    assert "full" in response.json()["detail"]


# --- the author's own published dashboards ---------------------------------


def test_an_author_sees_the_dashboards_they_published(author: TestClient, cube: None) -> None:
    author.post("/api/builder/publish", json={"spec_yaml": _yaml()})
    _as(author, OTHER).post("/api/builder/publish", json={"spec_yaml": _yaml(name="theirs")})

    mine = _as(author, AUTHOR).get("/api/builder/published").json()

    assert [entry["name"] for entry in mine] == ["my-ed-visits"]


def test_an_author_still_sees_their_dashboard_after_it_was_voted_down(
    author: TestClient, cube: None, engine: Engine
) -> None:
    """A hidden dashboard has dropped off the public page, and its author is the
    one person who can republish it fixed — so this list keeps it."""
    author.post("/api/builder/publish", json={"spec_yaml": _yaml()})
    now = datetime.now(UTC)
    with engine.begin() as connection:
        connection.execute(
            library.dashboards.update()
            .where(library.dashboards.c.name == "my-ed-visits")
            .values(downvotes=5, updated_at=now)
        )

    assert author.get("/api/dashboards").json() == []
    mine = author.get("/api/builder/published").json()
    assert [(entry["name"], entry["hidden"]) for entry in mine] == [("my-ed-visits", True)]


def test_an_authors_own_list_carries_the_source_to_edit(author: TestClient, cube: None) -> None:
    """Correcting a published dashboard is loading its spec back and publishing
    again — so the author's own listing carries the YAML a public entry never
    does."""
    author.post("/api/builder/publish", json={"spec_yaml": _yaml()})

    entry = author.get("/api/builder/published").json()[0]

    assert yaml.safe_load(entry["spec_yaml"])["vega"] == SPEC["vega"]
    # And it is still absent from the public listing of the same dashboard.
    assert "spec_yaml" not in author.get("/api/dashboards").json()[0]


# --- the field reference ---------------------------------------------------


def test_the_field_reference_is_the_semantic_layers_own_members(
    author: TestClient, cube: None
) -> None:
    """The same `/meta` the agent's `list_metrics` reads, so what an author can
    write is what the agent can ask for."""
    cubes = author.get("/api/semantic/cubes").json()

    assert [entry["name"] for entry in cubes] == ["ed_visits"]
    assert cubes[0]["measures"][0]["name"] == "ed_visits.avg_percent"
    assert cubes[0]["dimensions"][0]["type"] == "time"


def test_the_field_reference_is_cached(
    author: TestClient, cube: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every author opening the builder asks for it, and the shape changes when
    the cubes are redeployed rather than per request."""
    calls: list[int] = []

    async def counted(self: object) -> tuple[CubeInfo, ...]:
        calls.append(1)
        return CUBES

    monkeypatch.setattr("ohdp_agent.cube.CubeClient.list_metrics", counted)

    author.get("/api/semantic/cubes")
    author.get("/api/semantic/cubes")

    assert len(calls) == 1
