"""Stripe Checkout session creation and the subscription-events webhook
(`hub_api.billing`).

No network anywhere in this file. `stripe.checkout.Session.create` is a fake
that records the kwargs it is called with, for the checkout tests. The webhook
tests go through the *real* `stripe.Webhook.construct_event` — its signature
check is pure HMAC-SHA256 over the raw body, no network call — signed with
`_sign_payload` below, which replicates Stripe's `t=...,v1=...` scheme; that
is deliberate, since the signature check is the security boundary this
endpoint exists to enforce, and a mocked `construct_event` would not exercise
it at all.

What these tests protect:

  - checkout needs a signed-in user (401 otherwise) — charging a card is only
    meaningful once we know whose subscription to attach it to;
  - an unconfigured deployment answers 503 instead of half-working;
  - the session we create carries the buyer's *verified* email (the webhook
    reconciles on it) and the pinned API version;
  - the webhook rejects a bad signature, tolerates a redelivered event id, and
    turns `checkout.session.completed` / `customer.subscription.updated` /
    `customer.subscription.deleted` into the right `subscriptions` row and
    `users.tier`.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import stripe as stripe_lib
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine

from hub_api import auth, billing, db
from hub_api.main import app
from ohdp_shared import settings

PRICE = "price_pro_monthly"
API_VERSION = "2026-03-25.dahlia; custom_checkout_payment_form_preview=v1"
WEBHOOK_SECRET = "whsec_test_000"


@pytest.fixture(autouse=True)
def _configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_000", raising=False)
    monkeypatch.setattr(settings, "stripe_price_id", PRICE, raising=False)
    monkeypatch.setattr(settings, "stripe_api_version", API_VERSION, raising=False)
    monkeypatch.setattr(settings, "hub_base_url", "https://ohdp.example", raising=False)
    monkeypatch.setattr(settings, "stripe_webhook_secret", WEBHOOK_SECRET, raising=False)


@pytest.fixture
def client() -> Iterator[TestClient]:
    """A signed-in browser caller — `auth.get_current_user` overridden rather
    than a session cookie, since the endpoint needs only the identity."""
    app.dependency_overrides[auth.get_current_user] = lambda: auth.User(
        email="buyer@example.org", name="A Buyer", tier="free"
    )
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def stripe(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """A fake `Session.create` that records its kwargs and returns a client secret."""
    created: list[dict[str, Any]] = []

    class FakeSession:
        client_secret = "cs_test_123"

    def create(**kwargs: Any) -> FakeSession:
        created.append(kwargs)
        return FakeSession()

    monkeypatch.setattr(stripe_lib.checkout.Session, "create", create)
    return created


def test_checkout_requires_signing_in(stripe: list[dict[str, Any]]) -> None:
    response = TestClient(app).post("/api/billing/checkout")

    assert response.status_code == 401
    assert stripe == []


def test_checkout_returns_the_client_secret(
    client: TestClient, stripe: list[dict[str, Any]]
) -> None:
    response = client.post("/api/billing/checkout")

    assert response.status_code == 200
    assert response.json() == {"client_secret": "cs_test_123"}


def test_session_is_a_subscription_for_the_buyer(
    client: TestClient, stripe: list[dict[str, Any]]
) -> None:
    client.post("/api/billing/checkout")

    (session,) = stripe
    assert session["mode"] == "subscription"
    # Embedded Checkout page (`initEmbeddedCheckout`) needs a session created with
    # `ui_mode="embedded_page"`. We disable the redirect so the buyer stays on
    # /billing and the frontend's `onComplete` handler confirms the purchase in
    # place — and since redirects are off, Stripe forbids `return_url` too, so it
    # must be absent.
    assert session["ui_mode"] == "embedded_page"
    assert session["redirect_on_completion"] == "never"
    assert "return_url" not in session
    assert session["line_items"] == [{"price": PRICE, "quantity": 1}]
    # The trusted email comes from the session, never from the caller.
    assert session["customer_email"] == "buyer@example.org"
    assert session["metadata"] == {"email": "buyer@example.org", "tier": "plus"}
    # Every fixed_by_ui field intent is present exactly as specified.
    assert session["billing_address_collection"] == "auto"
    assert session["phone_number_collection"] == {"enabled": False}
    assert session["payment_method_collection"] == "always"
    assert session["automatic_tax"] == {"enabled": True}
    assert session["submit_type"] == "auto"
    assert session["name_collection"] == {
        "individual": {"enabled": True, "optional": True},
        "business": {"enabled": True, "optional": True},
    }
    assert session["saved_payment_method_options"] == {"payment_method_save": "enabled"}
    assert session["integration_identifier"] == "custom_embedded_web_0001"


def test_session_pins_the_stripe_api_version(
    client: TestClient, stripe: list[dict[str, Any]]
) -> None:
    client.post("/api/billing/checkout")

    assert stripe_lib.api_version == API_VERSION
    assert stripe_lib.api_key == "sk_test_000"


def test_unconfigured_billing_is_a_503(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, stripe: list[dict[str, Any]]
) -> None:
    monkeypatch.setattr(settings, "stripe_secret_key", "", raising=False)

    response = client.post("/api/billing/checkout")

    assert response.status_code == 503
    assert stripe == []


# --- Billing portal -----------------------------------------------------------


@pytest.fixture
def portal(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """A fake `billing_portal.Session.create` that records its kwargs and
    returns a portal URL."""
    created: list[dict[str, Any]] = []

    class FakeSession:
        url = "https://billing.stripe.com/session/test_123"

    def create(**kwargs: Any) -> FakeSession:
        created.append(kwargs)
        return FakeSession()

    monkeypatch.setattr(stripe_lib.billing_portal.Session, "create", create)
    return created


def _insert_subscription(
    engine: Engine,
    *,
    email: str = "buyer@example.org",
    customer_id: str,
    subscription_id: str,
    status: str = "active",
    created_at: datetime,
) -> None:
    with engine.begin() as connection:
        connection.execute(
            billing.subscriptions.insert().values(
                user_email=email,
                stripe_customer_id=customer_id,
                stripe_subscription_id=subscription_id,
                status=status,
                price_id=PRICE,
                cancel_at_period_end=False,
                created_at=created_at,
                updated_at=created_at,
            )
        )


def test_portal_requires_signing_in(portal: list[dict[str, Any]]) -> None:
    response = TestClient(app).post("/api/billing/portal")

    assert response.status_code == 401
    assert portal == []


def test_portal_404s_without_a_subscription(
    client: TestClient, engine: Engine, portal: list[dict[str, Any]]
) -> None:
    response = client.post("/api/billing/portal")

    assert response.status_code == 404
    assert portal == []


def test_portal_opens_for_the_buyers_customer(
    client: TestClient, engine: Engine, portal: list[dict[str, Any]]
) -> None:
    _insert_subscription(
        engine,
        customer_id="cus_1",
        subscription_id="sub_1",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    response = client.post("/api/billing/portal")

    assert response.status_code == 200
    assert response.json() == {"url": "https://billing.stripe.com/session/test_123"}
    (session,) = portal
    assert session["customer"] == "cus_1"
    assert session["return_url"] == "https://ohdp.example/billing"


def test_portal_uses_the_most_recent_subscription(
    client: TestClient, engine: Engine, portal: list[dict[str, Any]]
) -> None:
    _insert_subscription(
        engine,
        customer_id="cus_old",
        subscription_id="sub_old",
        status="canceled",
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    _insert_subscription(
        engine,
        customer_id="cus_new",
        subscription_id="sub_new",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    client.post("/api/billing/portal")

    (session,) = portal
    assert session["customer"] == "cus_new"


def test_portal_does_not_leak_another_buyers_customer(
    client: TestClient, engine: Engine, portal: list[dict[str, Any]]
) -> None:
    _insert_subscription(
        engine,
        email="someone-else@example.org",
        customer_id="cus_other",
        subscription_id="sub_other",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    response = client.post("/api/billing/portal")

    assert response.status_code == 404
    assert portal == []


def test_portal_unconfigured_billing_is_a_503(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
    engine: Engine,
    portal: list[dict[str, Any]],
) -> None:
    monkeypatch.setattr(settings, "stripe_secret_key", "", raising=False)

    response = client.post("/api/billing/portal")

    assert response.status_code == 503
    assert portal == []


# --- Webhook -----------------------------------------------------------------


def _sign_payload(payload: bytes, secret: str = WEBHOOK_SECRET) -> tuple[bytes, str]:
    """Replicates Stripe's `Stripe-Signature` header scheme
    (`t=<timestamp>,v1=<hex hmac-sha256>` over `f"{t}.{payload}"`) so the
    webhook tests exercise the real `stripe.Webhook.construct_event`, not a
    mock of it."""
    timestamp = int(time.time())
    signed = f"{timestamp}.".encode() + payload
    digest = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return payload, f"t={timestamp},v1={digest}"


def _event(event_id: str, event_type: str, obj: dict[str, Any]) -> bytes:
    return json.dumps(
        {"id": event_id, "type": event_type, "object": "event", "data": {"object": obj}}
    ).encode()


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    made = db.make_engine(f"sqlite:///{tmp_path}/billing.db")
    db.ensure_schema(made)
    app.state.engine = made
    try:
        yield made
    finally:
        app.state.engine = None


def test_webhook_rejects_a_bad_signature(engine: Engine) -> None:
    payload = _event("evt_1", "checkout.session.completed", {"mode": "subscription"})

    response = TestClient(app).post(
        "/api/billing/webhook/stripe",
        content=payload,
        headers={"stripe-signature": "t=1,v1=not-the-right-signature"},
    )

    assert response.status_code == 400


def test_webhook_unconfigured_is_a_503(monkeypatch: pytest.MonkeyPatch, engine: Engine) -> None:
    monkeypatch.setattr(settings, "stripe_webhook_secret", "", raising=False)
    payload, signature = _sign_payload(
        _event("evt_1", "checkout.session.completed", {"mode": "subscription"})
    )

    response = TestClient(app).post(
        "/api/billing/webhook/stripe", content=payload, headers={"stripe-signature": signature}
    )

    assert response.status_code == 503


def test_checkout_completed_activates_the_buyer(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            auth.users.insert().values(
                email="buyer@example.org",
                name="A Buyer",
                tier="free",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
                last_login_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
    payload, signature = _sign_payload(
        _event(
            "evt_checkout_1",
            "checkout.session.completed",
            {
                "id": "cs_test_1",
                "mode": "subscription",
                "customer": "cus_1",
                "subscription": "sub_1",
                "customer_email": "buyer@example.org",
                "metadata": {"email": "buyer@example.org", "tier": "plus"},
            },
        )
    )

    response = TestClient(app).post(
        "/api/billing/webhook/stripe", content=payload, headers={"stripe-signature": signature}
    )

    assert response.status_code == 200
    with engine.connect() as connection:
        user_tier = connection.execute(
            select(auth.users.c.tier).where(auth.users.c.email == "buyer@example.org")
        ).scalar_one()
        sub_row = connection.execute(
            select(billing.subscriptions).where(
                billing.subscriptions.c.stripe_subscription_id == "sub_1"
            )
        ).one()
    assert user_tier == "plus"
    assert sub_row.user_email == "buyer@example.org"
    assert sub_row.stripe_customer_id == "cus_1"
    assert sub_row.status == "active"


def test_webhook_ignores_a_redelivered_event(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            auth.users.insert().values(
                email="buyer@example.org",
                name="A Buyer",
                tier="free",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
                last_login_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
    raw = _event(
        "evt_checkout_2",
        "checkout.session.completed",
        {
            "mode": "subscription",
            "customer": "cus_1",
            "subscription": "sub_1",
            "metadata": {"email": "buyer@example.org"},
        },
    )
    client = TestClient(app)
    payload, signature = _sign_payload(raw)
    client.post(
        "/api/billing/webhook/stripe", content=payload, headers={"stripe-signature": signature}
    )
    # Between the two deliveries, an operator manually reverts the tier — a
    # redelivery of the same event id must not silently reapply the change.
    with engine.begin() as connection:
        connection.execute(
            auth.users.update().where(auth.users.c.email == "buyer@example.org").values(tier="free")
        )

    payload, signature = _sign_payload(raw)
    response = client.post(
        "/api/billing/webhook/stripe", content=payload, headers={"stripe-signature": signature}
    )

    assert response.status_code == 200
    with engine.connect() as connection:
        user_tier = connection.execute(
            select(auth.users.c.tier).where(auth.users.c.email == "buyer@example.org")
        ).scalar_one()
    assert user_tier == "free"


def test_subscription_updated_changes_status_and_tier(engine: Engine) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    with engine.begin() as connection:
        connection.execute(
            auth.users.insert().values(
                email="buyer@example.org", name="", tier="plus", created_at=now, last_login_at=now
            )
        )
        connection.execute(
            billing.subscriptions.insert().values(
                user_email="buyer@example.org",
                stripe_customer_id="cus_1",
                stripe_subscription_id="sub_1",
                status="active",
                price_id=PRICE,
                cancel_at_period_end=False,
                created_at=now,
                updated_at=now,
            )
        )
    payload, signature = _sign_payload(
        _event(
            "evt_updated_1",
            "customer.subscription.updated",
            {
                "id": "sub_1",
                "status": "past_due",
                "current_period_end": 1893456000,
                "cancel_at_period_end": False,
                "items": {"data": [{"price": {"id": PRICE}}]},
            },
        )
    )

    response = TestClient(app).post(
        "/api/billing/webhook/stripe", content=payload, headers={"stripe-signature": signature}
    )

    assert response.status_code == 200
    with engine.connect() as connection:
        user_tier = connection.execute(
            select(auth.users.c.tier).where(auth.users.c.email == "buyer@example.org")
        ).scalar_one()
        status = connection.execute(
            select(billing.subscriptions.c.status).where(
                billing.subscriptions.c.stripe_subscription_id == "sub_1"
            )
        ).scalar_one()
    # past_due is still a plus tier — Stripe's dunning grace period.
    assert user_tier == "plus"
    assert status == "past_due"


def test_subscription_deleted_drops_the_buyer_to_free(engine: Engine) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    with engine.begin() as connection:
        connection.execute(
            auth.users.insert().values(
                email="buyer@example.org", name="", tier="plus", created_at=now, last_login_at=now
            )
        )
        connection.execute(
            billing.subscriptions.insert().values(
                user_email="buyer@example.org",
                stripe_customer_id="cus_1",
                stripe_subscription_id="sub_1",
                status="active",
                price_id=PRICE,
                cancel_at_period_end=False,
                created_at=now,
                updated_at=now,
            )
        )
    payload, signature = _sign_payload(
        _event("evt_deleted_1", "customer.subscription.deleted", {"id": "sub_1"})
    )

    response = TestClient(app).post(
        "/api/billing/webhook/stripe", content=payload, headers={"stripe-signature": signature}
    )

    assert response.status_code == 200
    with engine.connect() as connection:
        user_tier = connection.execute(
            select(auth.users.c.tier).where(auth.users.c.email == "buyer@example.org")
        ).scalar_one()
        status = connection.execute(
            select(billing.subscriptions.c.status).where(
                billing.subscriptions.c.stripe_subscription_id == "sub_1"
            )
        ).scalar_one()
    assert user_tier == "free"
    assert status == "canceled"


def test_unknown_event_type_is_accepted_and_ignored(engine: Engine) -> None:
    payload, signature = _sign_payload(_event("evt_other_1", "invoice.paid", {"id": "in_1"}))

    response = TestClient(app).post(
        "/api/billing/webhook/stripe", content=payload, headers={"stripe-signature": signature}
    )

    assert response.status_code == 200