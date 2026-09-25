"""Stripe Checkout — embedded form — for the paid tier (M4, first half).

Sells the $5/mo Pro subscription through Stripe's **embedded Checkout** page:
the server creates a Checkout Session and returns only its `client_secret`; the
browser (apps/web) loads Stripe.js and mounts the Stripe-hosted Checkout page
in-page via `stripe.initEmbeddedCheckout({ clientSecret })`. No card details
ever touch this process, and the customer never leaves `/billing`.

The other half of billing — activating the tier once a customer is *actually*
paying, i.e. the `checkout.session.completed` webhook that bumps `users.tier` —
is deliberately deferred (see the per-milestone notes at the bottom of
`main.py`). Until that webhook exists, charging a card does not by itself grant
Pro; a `tier` change still takes a manual UPDATE, the same as before billing
shipped.

A few choices worth writing down:

  - **Embedded form, not hosted Checkout or Payment Links.** The checkout must
    only be reachable by someone who already exists in our own `users` table and
    is signed in. The session is created for *this* request's verified email
    (`customer_email` is stamped from the signed session cookie, never from a
    request body), so the (future) webhook can reconcile back to `users.email`.
  - **Embedded Checkout page (`ui_mode="embedded_page"`).** The client renders the
    Stripe-hosted Checkout page in-page via `stripe.initEmbeddedCheckout({ clientSecret })`
    and surfaces the "subscribed" state through that API's `onComplete` event. An
    `embedded_page` session is redirect-based by default and would then require a
    `return_url` — but we pass `redirect_on_completion="never"` instead, so the
    customer stays on `/billing` (and Stripe forbids passing `return_url` when
    redirects are off, so we don't).
  - **The API version is pinned** to `settings.stripe_api_version`. That version
    carries the preview flag `saved_payment_method_options` needs on the embedded
    Checkout page. Keep it in lock-step with the js.stripe.com build loaded in
    apps/web/index.html.
  - **No card details ever touch this process.** We create a session and return
    the `client_secret`; Stripe.js collects and tokenizes the card on
    js.stripe.com.
"""

from __future__ import annotations

from typing import Annotated

import stripe
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from hub_api.auth import User, get_current_user
from ohdp_shared import get_logger, settings

log = get_logger(__name__)

router = APIRouter(prefix="/api/billing", tags=["billing"])


class CheckoutStart(BaseModel):
    """Response to `POST /api/billing/checkout` — the session's client secret,
    which the embedded Checkout page (`stripe.initEmbeddedCheckout`) needs to
    mount the Stripe-hosted Checkout page in-page."""

    client_secret: str


def _require_stripe() -> None:
    """Fail loudly (503) before Stripe is called if billing cannot work, rather
    than half-creating a session that is missing its key or its price."""
    if not settings.stripe_secret_key:
        raise HTTPException(503, "Billing is not configured on this deployment.")
    if not settings.stripe_price_id:
        raise HTTPException(
            503, "Checkout is not configured: no subscription price is set."
        )


@router.post("/checkout", summary="Start a Pro subscription Checkout")
def start_checkout(user: Annotated[User, Depends(get_current_user)]) -> CheckoutStart:
    """Create an embedded Checkout Session for this signed-in user and return
    its `client_secret`. The frontend hands that secret to Stripe.js' embedded
    Checkout page (`stripe.initEmbeddedCheckout`), which renders and confirms
    the payment in-page — this endpoint does not redirect anywhere.
    """
    _require_stripe()
    # Both are module state on the `stripe` package; setting on each call keeps
    # them right even if some other thread/import touched them, and lets tests
    # drive them without a reload.
    stripe.api_key = settings.stripe_secret_key
    stripe.api_version = settings.stripe_api_version
    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            # Embedded Checkout page, mounted in-page via Stripe.js'
            # `initEmbeddedCheckout` (the docs' GA embedded Checkout quickstart).
            # An `embedded_page` session is redirect-based by default and then
            # demands a `return_url`; we set `redirect_on_completion="never"` to
            # keep the customer on /billing (the frontend's `onComplete` handler
            # confirms the purchase in place). With redirects disabled, Stripe
            # *forbids* passing `return_url` too — these sessions need neither.
            ui_mode="embedded_page",
            redirect_on_completion="never",
            line_items=[{"price": settings.stripe_price_id, "quantity": 1}],
            billing_address_collection="auto",
            phone_number_collection={"enabled": False},
            name_collection={
                "individual": {"enabled": True, "optional": True},
                "business": {"enabled": True, "optional": True},
            },
            payment_method_collection="always",
            automatic_tax={"enabled": True},
            submit_type="auto",
            integration_identifier="custom_embedded_web_0001",
            saved_payment_method_options={"payment_method_save": "enabled"},
            # Who is paying, stamped on the session so the (future) webhook maps
            # it back without trusting a page redirect.
            customer_email=user.email,
            metadata={"email": user.email, "tier": "paid"},
        )
    except stripe.StripeError as exc:
        log.warning("checkout_session_failed", email=user.email, error=str(exc))
        raise HTTPException(400, "Stripe could not create a checkout session.") from exc
    log.info("checkout_session_created", email=user.email)
    return CheckoutStart(client_secret=str(session.client_secret))