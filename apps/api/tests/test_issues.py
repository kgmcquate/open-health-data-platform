"""The issue-reporting tool, exercised through the mounted /tools app.

No GitHub: the API is a transport stub. What these tests protect is everything
around the call — that the two identity paths stay distinct, that the spec Open
WebUI reads exposes only the intended operations and not the chat endpoint, that a duplicate
does not become a second issue, and that a pasted credential does not reach a
public repository.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from hub_api import db, issues
from hub_api.main import app

TOKEN = "tools-token"
REPO = "kgmcquate/open-health-data-platform"


class FakeGitHub:
    """Stands in for api.github.com. Records what would have been created."""

    def __init__(self, existing: list[dict[str, Any]] | None = None) -> None:
        self.existing = existing or []
        self.created: list[dict[str, Any]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=self.existing)
        payload = json.loads(request.content)
        self.created.append(payload)
        number = 100 + len(self.created)
        return httpx.Response(
            201,
            json={"number": number, "html_url": f"https://github.com/{REPO}/issues/{number}"},
        )


@pytest.fixture(autouse=True)
def _configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(issues.settings, "github_token", "gh-token", raising=False)
    monkeypatch.setattr(issues.settings, "github_issues_repo", REPO, raising=False)
    monkeypatch.setattr(issues.settings, "tools_auth_token", TOKEN, raising=False)
    # The rate limiter is module state; a leaked window from one test would
    # 429 the next one for an hour.
    issues._recent_reports.clear()


@pytest.fixture
def github(monkeypatch: pytest.MonkeyPatch) -> FakeGitHub:
    fake = FakeGitHub()
    real_client = httpx.AsyncClient

    def patched(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(fake.handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(issues.httpx, "AsyncClient", patched)
    return fake


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    # `report_issue`'s daily cap (issues.get_engine) reads real rows, the same
    # way test_chat_threads.py's fixture backs chat.get_engine — a sqlite file
    # rather than a mock, since the thing under test is the COUNT query itself.
    # Overridden on both `app` (the Support page's route) and `issues.tools_app`
    # (the chat surface's route): two FastAPI instances, two override dicts.
    engine = db.make_engine(f"sqlite:///{tmp_path}/test.db")
    db.ensure_schema(engine)
    app.dependency_overrides[issues.get_engine] = lambda: engine
    issues.tools_app.dependency_overrides[issues.get_engine] = lambda: engine
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        issues.tools_app.dependency_overrides.clear()


REPORT = {
    "title": "Air quality averages look wrong for Montana",
    "body": "The 2023 average is an order of magnitude above every neighbouring state.",
    "kind": "data-quality",
}


def test_chatbot_files_anonymously(client: TestClient, github: FakeGitHub) -> None:
    response = client.post(
        "/tools/report_issue", json=REPORT, headers={"Authorization": f"Bearer {TOKEN}"}
    )
    assert response.status_code == 200
    assert response.json()["duplicate"] is False

    (created,) = github.created
    assert "source:chatbot" in created["labels"]
    assert issues.LABEL in created["labels"]
    # The bearer token says the *caller* is Open WebUI; it says nothing about
    # which human typed, and the issue must not imply otherwise.
    assert "anonymous chat user" in created["body"]


def test_web_user_is_attributed_to_the_verified_email(
    client: TestClient, github: FakeGitHub
) -> None:
    # A browser's identity comes from the hub's own signed session cookie
    # (hub_api.auth), not a header — dependency_overrides is the same
    # substitute test_chat_threads.py uses for chat.get_user_email.
    # `tools_app` is its own FastAPI instance (mounted, not included), so the
    # override belongs on it, not on `app`.
    issues.tools_app.dependency_overrides[issues.get_reporter] = lambda: issues.Reporter(
        source="web", email="a@example.org"
    )
    try:
        response = client.post("/tools/report_issue", json=REPORT)
    finally:
        issues.tools_app.dependency_overrides.clear()
    assert response.status_code == 200

    (created,) = github.created
    assert "source:web" in created["labels"]
    assert "a@example.org" in created["body"]


def test_unauthenticated_caller_is_refused(client: TestClient, github: FakeGitHub) -> None:
    assert client.post("/tools/report_issue", json=REPORT).status_code == 401
    assert (
        client.post(
            "/tools/report_issue", json=REPORT, headers={"Authorization": "Bearer wrong"}
        ).status_code
        == 401
    )
    assert github.created == []


def test_duplicate_title_returns_the_existing_issue(client: TestClient, github: FakeGitHub) -> None:
    github.existing = [
        {
            "number": 7,
            "title": "Air-quality averages look wrong for Montana!",
            "html_url": f"https://github.com/{REPO}/issues/7",
        }
    ]
    response = client.post(
        "/tools/report_issue", json=REPORT, headers={"Authorization": f"Bearer {TOKEN}"}
    )
    assert response.status_code == 200
    assert response.json() == {
        "number": 7,
        "url": f"https://github.com/{REPO}/issues/7",
        "duplicate": True,
    }
    assert github.created == []


def test_credentials_are_redacted_before_publication(
    client: TestClient, github: FakeGitHub
) -> None:
    leaked = "ghp_" + "a" * 36
    client.post(
        "/tools/report_issue",
        json={**REPORT, "body": f"I ran the export and got an error. My token is {leaked}."},
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    (created,) = github.created
    assert leaked not in created["body"]
    assert "[redacted]" in created["body"]


def test_long_report_is_truncated_not_rejected(client: TestClient, github: FakeGitHub) -> None:
    response = client.post(
        "/tools/report_issue",
        json={**REPORT, "body": "transcript. " * 1000},
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert response.status_code == 200
    # The footer is appended after truncation, so the body exceeds MAX_BODY by
    # exactly that much and no more.
    (created,) = github.created
    assert "…" in created["body"]
    assert len(created["body"]) < issues.MAX_BODY + 400


def test_rate_limit_stops_a_flood(client: TestClient, github: FakeGitHub) -> None:
    headers = {"Authorization": f"Bearer {TOKEN}"}
    for n in range(issues.RATE_LIMIT):
        body = {**REPORT, "title": f"Distinct problem number {n} in the warehouse"}
        assert client.post("/tools/report_issue", json=body, headers=headers).status_code == 200

    refused = client.post(
        "/tools/report_issue",
        json={**REPORT, "title": "One problem too many in the warehouse"},
        headers=headers,
    )
    assert refused.status_code == 429
    assert len(github.created) == issues.RATE_LIMIT


def _report_as(client: TestClient, *, email: str, tier: str, title: str) -> Any:
    issues.tools_app.dependency_overrides[issues.get_reporter] = lambda: issues.Reporter(
        source="web", email=email, tier=tier
    )
    try:
        return client.post("/tools/report_issue", json={**REPORT, "title": title})
    finally:
        del issues.tools_app.dependency_overrides[issues.get_reporter]


def test_daily_cap_limits_a_free_reporter_to_one(client: TestClient, github: FakeGitHub) -> None:
    first = _report_as(client, email="a@example.org", tier="free", title="Distinct problem one")
    assert first.status_code == 200

    second = _report_as(client, email="a@example.org", tier="free", title="Distinct problem two")
    assert second.status_code == 429
    assert len(github.created) == 1

    # A different reporter has their own, untouched allowance.
    other = _report_as(client, email="b@example.org", tier="free", title="Distinct problem three")
    assert other.status_code == 200


def test_daily_cap_is_higher_for_a_plus_reporter(client: TestClient, github: FakeGitHub) -> None:
    for n in range(issues.settings.plus_daily_issues):
        response = _report_as(client, email="a@example.org", tier="plus", title=f"Plus problem {n}")
        assert response.status_code == 200

    refused = _report_as(
        client, email="a@example.org", tier="plus", title="One plus problem too many"
    )
    assert refused.status_code == 429
    assert len(github.created) == issues.settings.plus_daily_issues


def test_duplicate_report_does_not_consume_the_daily_cap(
    client: TestClient, github: FakeGitHub
) -> None:
    github.existing = [
        {
            "number": 7,
            "title": "Air-quality averages look wrong for Montana!",
            "html_url": f"https://github.com/{REPO}/issues/7",
        }
    ]
    duplicate = _report_as(client, email="a@example.org", tier="free", title=REPORT["title"])
    assert duplicate.status_code == 200
    assert duplicate.json()["duplicate"] is True

    fresh = _report_as(client, email="a@example.org", tier="free", title="A genuinely new problem")
    assert fresh.status_code == 200
    assert len(github.created) == 1


def test_daily_cap_does_not_apply_to_anonymous_chatbot_reports(
    client: TestClient, github: FakeGitHub
) -> None:
    """A chatbot reporter has no email to key the daily cap on — it stays
    covered by the hourly rate limit alone (test_rate_limit_stops_a_flood)."""
    headers = {"Authorization": f"Bearer {TOKEN}"}
    for n in range(issues.settings.free_daily_issues + 1):
        body = {**REPORT, "title": f"Anonymous problem {n}"}
        assert client.post("/tools/report_issue", json=body, headers=headers).status_code == 200
    assert len(github.created) == issues.settings.free_daily_issues + 1


def test_unconfigured_deployment_refuses_instead_of_half_working(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(issues.settings, "github_token", "", raising=False)
    response = client.post(
        "/tools/report_issue", json=REPORT, headers={"Authorization": f"Bearer {TOKEN}"}
    )
    assert response.status_code == 503


def test_tool_spec_exposes_only_the_allowlisted_tools(client: TestClient) -> None:
    """The narrowing that keeps `/api/chat` out of the model's hands.

    Open WebUI turns every operation in the spec it reads into a callable tool,
    so this assertion is the access control, not a tidiness check. It is an
    exact set rather than a subset check on purpose: a route added to the
    mounted app is a tool handed to the chat model, and that should be a
    decision someone made here rather than a side effect of a new endpoint.
    """
    spec = client.get("/tools/openapi.json").json()
    assert {
        operation["operationId"]
        for methods in spec["paths"].values()
        for operation in methods.values()
    } == {
        "report_issue",
        "render_dashboard",
        "save_dashboard",
        "get_dashboard",
    }
    assert spec["paths"]["/report_issue"]["post"]["operationId"] == "report_issue"
