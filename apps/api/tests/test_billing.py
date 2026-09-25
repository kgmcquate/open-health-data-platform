"""Stripe Checkout session creation (`hub_api.billing`).

No network: `stripe.checkout.Session.create` is a fake that records the kwargs
it is called with. What these tests protect is the contract around the call:

  - checkout needs a signed-in user (401 otherwise) — charging a card is only
    meaningful once we know whose subscription to attach it to;
  - an unconfigured deployment answers 503 instead of half-working;
  - the session we create carries the buyer's *verified* email (the future
    webhook reconciles on it) and the pinned API version.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
import stripe as stripe_lib
from fastapi.testclient import TestClient

from hub_api import auth
from hub_api.main import app
from ohdp_shared import settings

PRICE = "price_pro_monthly"
API_VERSION = "2026-03-25.dahlia; custom_checkout_payment_form_preview=v1"


@pytest.fixture(autouse=True)
def _configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_000", raising=False)
    monkeypatch.setattr(settings, "stripe_price_id", PRICE, raising=False)
    monkeypatch.setattr(settings, "stripe_api_version", API_VERSION, raising=False)
    monkeypatch.setattr(settings, "hub_base_url", "https://ohdp.example", raising=False)


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
    assert session["metadata"] == {"email": "buyer@example.org", "tier": "paid"}
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