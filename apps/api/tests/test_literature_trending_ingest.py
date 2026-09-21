"""`POST /api/internal/literature/trending` (hub_api.content) — the write path
the Dagster trending-literature sync calls. Bearer-token gated, upserts by
`url`, and clears `trending` on rows this run didn't include."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from hub_api import db
from hub_api.content import curated_literature, ensure_indexes
from hub_api.main import app
from ohdp_shared import settings

TOKEN = "s3cret-ingest-token"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(settings, "app_database_url", f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setattr(settings, "internal_ingest_token", TOKEN)
    with TestClient(app) as test_client:
        yield test_client


def _item(**overrides: object) -> dict[str, object]:
    base = {
        "title": "A Paper About Something",
        "authors": "A. One, B. Two",
        "venue": "Journal of Testing",
        "year": 2023,
        "url": "https://doi.org/10.1000/xyz",
        "doi": "10.1000/xyz",
        "summary": "This is an abstract.",
        "tags": ["Chronic Disease"],
    }
    base.update(overrides)
    return base


def test_rejects_missing_bearer_token(client: TestClient) -> None:
    response = client.post("/api/internal/literature/trending", json=[_item()])
    assert response.status_code == 401


def test_rejects_wrong_bearer_token(client: TestClient) -> None:
    response = client.post(
        "/api/internal/literature/trending",
        json=[_item()],
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert response.status_code == 401


def test_inserts_new_items_as_trending(client: TestClient) -> None:
    response = client.post(
        "/api/internal/literature/trending",
        json=[_item()],
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert response.status_code == 200
    assert response.json() == {"synced": 1}

    listing = client.get("/api/literature")
    assert listing.status_code == 200
    [row] = listing.json()
    assert row["title"] == "A Paper About Something"
    assert row["trending"] is True


def test_upserts_by_url_instead_of_duplicating(client: TestClient) -> None:
    headers = {"Authorization": f"Bearer {TOKEN}"}
    client.post("/api/internal/literature/trending", json=[_item()], headers=headers)
    client.post(
        "/api/internal/literature/trending",
        json=[_item(title="Updated Title", tags=["Respiratory"])],
        headers=headers,
    )

    listing = client.get("/api/literature")
    rows = listing.json()
    assert len(rows) == 1
    assert rows[0]["title"] == "Updated Title"
    assert rows[0]["tags"] == ["Respiratory"]


def test_clears_trending_flag_on_papers_that_drop_out_of_the_run(client: TestClient) -> None:
    headers = {"Authorization": f"Bearer {TOKEN}"}
    client.post(
        "/api/internal/literature/trending",
        json=[_item(url="https://doi.org/10.1/a", title="Paper A")],
        headers=headers,
    )
    # Second run only includes a different paper — "Paper A" fell out of the
    # ranking and should lose its trending badge, not be deleted.
    client.post(
        "/api/internal/literature/trending",
        json=[_item(url="https://doi.org/10.1/b", title="Paper B")],
        headers=headers,
    )

    listing = {row["title"]: row for row in client.get("/api/literature").json()}
    assert listing["Paper A"]["trending"] is False
    assert listing["Paper B"]["trending"] is True


def test_url_unique_index_created_idempotently(tmp_path: Path) -> None:
    """`ensure_indexes` must be safe to call repeatedly, including against a
    database whose table already existed (the pre-migration case)."""
    engine = db.make_engine(f"sqlite:///{tmp_path}/idempotent.db")
    db.ensure_schema(engine)
    ensure_indexes(engine)
    ensure_indexes(engine)  # second call must not raise

    with engine.connect() as connection:
        assert connection.execute(select(curated_literature.c.id)).all() == []
