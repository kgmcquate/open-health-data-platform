"""Stripe Checkout (M4) — session creation plus the subscription-events webhook
that activates and maintains the Plus tier.

Sells the $5/mo Plus subscription through Stripe's **embedded Checkout** page:
the server creates a Checkout Session and returns only its `client_secret`; the
browser (apps/web) loads Stripe.js and mounts the Stripe-hosted Checkout page
in-page via `stripe.initEmbeddedCheckout({ clientSecret })`. No card details
ever touch this process, and the customer never leaves `/billing`.

`POST /api/billing/webhook/stripe` is the other half: Stripe's push notifications for
`checkout.session.completed`, `customer.subscription.updated`, and
`customer.subscription.deleted` land there, get verified against
`STRIPE_WEBHOOK_SECRET`, and are recorded into `subscriptions` (one row per
Stripe subscription id) before `hub_api.auth.set_user_tier` flips the buyer's
`users.tier`. `stripe_events` is a bare insert-or-skip idempotency log keyed on
Stripe's event id, because Stripe's delivery guarantee is "at least once."

`POST /api/billing/portal` is how a Plus buyer manages what they already
bought — canceling, swapping the card on file, or pulling an invoice. It opens
a Stripe-hosted **Billing Portal** session for the buyer's Stripe customer id
(looked up from their most recent `subscriptions` row) rather than building
any of that here; the portal itself posts back through the same
`customer.subscription.*` webhook events above, so a cancellation made there
flows through the exact same tier-update path as one Stripe triggers for any
other reason (a failed renewal, dunning running out, ...).

A few choices worth writing down:

  - **Embedded form, not hosted Checkout or Payment Links.** The checkout must
    only be reachable by someone who already exists in our own `users` table and
    is signed in. The session is created for *this* request's verified email
    (`customer_email` is stamped from the signed session cookie, never from a
    request body), so the webhook can reconcile back to `users.email`.
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

from datetime import UTC, datetime
from typing import Annotated, Any

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import Boolean, Column, DateTime, Integer, String, Table, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from hub_api import db
from hub_api.auth import User, get_current_user, set_user_tier
from ohdp_shared import get_logger, settings

log = get_logger(__name__)

router = APIRouter(prefix="/api/billing", tags=["billing"])

# Lives on db.metadata so `db.ensure_schema` (main.py's lifespan) creates it
# with the rest of the app's tables. Not a foreign key to `auth.users.email`:
# same reasoning as `chat_turns.user_email` — SQLite in local dev doesn't
# enforce one anyway, and in Postgres it would only buy a constraint this
# webhook already keeps consistent by construction.
subscriptions = Table(
    "subscriptions",
    db.metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_email", String(320), nullable=False, index=True),
    Column("stripe_customer_id", String(64), nullable=False, index=True),
    # Unique: this row IS the subscription, one per Stripe subscription id.
    # `customer.subscription.updated`/`.deleted` key off it to find the row a
    # later event should update rather than insert a duplicate of.
    Column("stripe_subscription_id", String(64), nullable=False, unique=True, index=True),
    # Stripe's own status string verbatim (active/trialing/past_due/canceled/
    # unpaid/incomplete/incomplete_expired/paused) — not our "free"/"plus",
    # which `_tier_for_status` below derives from it.
    Column("status", String(32), nullable=False),
    Column("price_id", String(64), nullable=False, default=""),
    Column("current_period_end", DateTime(timezone=True), nullable=True),
    # Derived, not Stripe's `cancel_at_period_end` verbatim — see `cancel_at`
    # just below for why that field can't be trusted alone.
    Column("cancel_at_period_end", Boolean, nullable=False, default=False),
    # Stripe's own `cancel_at`: the timestamp a *scheduled* (not yet final)
    # cancellation will take effect, or null. Under `billing_mode: "flexible"`
    # (this account's), the Dashboard/Portal's "cancel at end of billing
    # period" action sets only this field — `cancel_at_period_end` stays
    # `false` the whole time, which is the legacy flag from before `cancel_at`
    # existed and apparently isn't set by that flow anymore. So
    # `cancel_at_period_end` above is derived as "`cancel_at` is set and the
    # subscription hasn't actually ended yet", not read off Stripe's field of
    # the same name — see `_handle_subscription_updated`.
    Column("cancel_at", DateTime(timezone=True), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

# One row per processed Stripe event id. Stripe's delivery guarantee is "at
# least once" (a retried delivery, a redelivered test event, our own 5xx), so
# insert-or-skip on `id` (Stripe's `evt_...`) is the whole idempotency guard —
# nothing here is ever updated or read back for its own sake.
stripe_events = Table(
    "stripe_events",
    db.metadata,
    Column("id", String(64), primary_key=True),
    Column("type", String(64), nullable=False),
    Column("received_at", DateTime(timezone=True), nullable=False),
)

# past_due keeps Plus live through Stripe's dunning retries — a grace period,
# not a loophole; Stripe has already emailed the buyer and will retry the card
# a few times before giving up. Every other status is not currently paying.
_PLUS_STATUSES = {"active", "trialing", "past_due"}


def _tier_for_status(status: str) -> str:
    return "plus" if status in _PLUS_STATUSES else "free"


class CheckoutStart(BaseModel):
    """Response to `POST /api/billing/checkout` — the session's client secret,
    which the embedded Checkout page (`stripe.initEmbeddedCheckout`) needs to
    mount the Stripe-hosted Checkout page in-page."""

    client_secret: str


class PortalStart(BaseModel):
    """Response to `POST /api/billing/portal` — a Stripe-hosted Billing Portal
    URL. Unlike Checkout, the portal is not embedded: the browser navigates
    there directly, and Stripe sends the buyer back to `return_url` when
    they're done."""

    url: str


def _require_stripe() -> None:
    """Fail loudly (503) before Stripe is called if billing cannot work, rather
    than half-creating a session that is missing its key or its price."""
    if not settings.stripe_secret_key:
        raise HTTPException(503, "Billing is not configured on this deployment.")
    if not settings.stripe_price_id:
        raise HTTPException(503, "Checkout is not configured: no subscription price is set.")


@router.post("/checkout", summary="Start a Plus subscription Checkout")
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
            # Who is paying, stamped on the session so the webhook's
            # `checkout.session.completed` handler maps it back to `users.email`
            # without trusting a page redirect.
            customer_email=user.email,
            metadata={"email": user.email, "tier": "plus"},
        )
    except stripe.StripeError as exc:
        log.warning("checkout_session_failed", email=user.email, error=str(exc))
        raise HTTPException(400, "Stripe could not create a checkout session.") from exc
    log.info("checkout_session_created", email=user.email)
    return CheckoutStart(client_secret=str(session.client_secret))


def _latest_stripe_customer_id(engine: Engine, *, email: str) -> str | None:
    """The Stripe customer id behind `email`'s most recent subscription, or
    `None` if they've never checked out. There's one Stripe customer per buyer
    in practice (`checkout.session.completed` always reuses it), so "most
    recent row" and "the buyer's customer id" agree; `created_at desc` is just
    the tiebreak if that ever stops being true.
    """
    statement = (
        select(subscriptions.c.stripe_customer_id)
        .where(subscriptions.c.user_email == email)
        .order_by(subscriptions.c.created_at.desc())
        .limit(1)
    )
    with engine.connect() as connection:
        row = connection.execute(statement).first()
    return str(row.stripe_customer_id) if row else None


@router.post("/portal", summary="Open the Stripe Billing Portal")
def start_portal(request: Request, user: Annotated[User, Depends(get_current_user)]) -> PortalStart:
    """Create a Stripe Billing Portal session for this signed-in user and
    return its URL. The portal is Stripe's own hosted UI for everything past
    the initial purchase — canceling, changing the card on file, and viewing
    invoices — so none of that needs a bespoke page here. 404 if this buyer
    has never checked out, since there is no Stripe customer to open a portal
    session for.
    """
    _require_stripe()
    engine: Engine | None = getattr(request.app.state, "engine", None)
    if engine is None:
        raise HTTPException(503, "Billing storage is not available.")
    customer_id = _latest_stripe_customer_id(engine, email=user.email)
    if customer_id is None:
        raise HTTPException(404, "No subscription found for this account.")

    stripe.api_key = settings.stripe_secret_key
    stripe.api_version = settings.stripe_api_version
    try:
        session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=f"{settings.hub_base_url.rstrip('/')}/billing",
        )
    except stripe.StripeError as exc:
        log.warning("portal_session_failed", email=user.email, error=str(exc))
        raise HTTPException(400, "Stripe could not open the billing portal.") from exc
    log.info("portal_session_created", email=user.email)
    return PortalStart(url=str(session.url))


def _record_event_once(engine: Engine, *, event_id: str, event_type: str) -> bool:
    """Insert `event_id` into `stripe_events`. True the first time we see it;
    False if a prior delivery already claimed it (Stripe's guarantee is "at
    least once", so retried/redelivered events are expected, not a bug)."""
    try:
        with engine.begin() as connection:
            connection.execute(
                stripe_events.insert().values(
                    id=event_id, type=event_type, received_at=datetime.now(UTC)
                )
            )
        return True
    except IntegrityError:
        return False


def _handle_checkout_completed(engine: Engine, session_obj: dict[str, Any]) -> None:
    """`checkout.session.completed` — the buyer finished paying. Creates the
    `subscriptions` row and grants Plus immediately; the authoritative status,
    price, and period end are filled in by the `customer.subscription.*`
    events Stripe sends alongside it, since this session object doesn't carry
    them without an `expand` we didn't ask for.
    """
    if session_obj.get("mode") != "subscription":
        return
    email = (session_obj.get("metadata") or {}).get("email") or session_obj.get("customer_email")
    customer_id = session_obj.get("customer")
    subscription_id = session_obj.get("subscription")
    if not (email and customer_id and subscription_id):
        log.warning("stripe_checkout_completed_missing_fields", session_id=session_obj.get("id"))
        return

    now = datetime.now(UTC)
    with engine.begin() as connection:
        existing = connection.execute(
            select(subscriptions.c.id).where(
                subscriptions.c.stripe_subscription_id == subscription_id
            )
        ).first()
        if existing is None:
            connection.execute(
                subscriptions.insert().values(
                    user_email=email,
                    stripe_customer_id=customer_id,
                    stripe_subscription_id=subscription_id,
                    status="active",
                    price_id=settings.stripe_price_id,
                    cancel_at_period_end=False,
                    created_at=now,
                    updated_at=now,
                )
            )
        else:
            connection.execute(
                subscriptions.update()
                .where(subscriptions.c.stripe_subscription_id == subscription_id)
                .values(user_email=email, stripe_customer_id=customer_id, updated_at=now)
            )
    set_user_tier(engine, email=email, tier="plus")
    log.info("stripe_subscription_activated", email=email, subscription_id=subscription_id)


def _handle_subscription_updated(engine: Engine, sub_obj: dict[str, Any]) -> None:
    """`customer.subscription.updated` — a renewal, a status change (e.g. into
    `past_due` when a card fails), or a plan change. Updates the existing
    `subscriptions` row and re-derives `users.tier` from the new status.
    """
    subscription_id = sub_obj.get("id")
    status = sub_obj.get("status", "")
    items = ((sub_obj.get("items") or {}).get("data")) or []
    price_id = (items[0].get("price") or {}).get("id", "") if items else ""
    # Not `sub_obj["current_period_end"]` — Stripe moved period tracking onto
    # each subscription item (to support items on different billing cycles),
    # so the pinned API version's `Subscription` object no longer carries it
    # at the top level at all. Every item shares one price/cycle here (we
    # never sell more than one), so the first item's value is the
    # subscription's.
    period_end = items[0].get("current_period_end") if items else None
    # Stripe's own `cancel_at_period_end` stays `false` for a Dashboard/Portal
    # "cancel at end of billing period" under `billing_mode: "flexible"` — it
    # only sets `cancel_at` (a timestamp) instead, so trusting the boolean
    # alone missed every one of those cancellations. `cancel_at` is null once
    # there is nothing scheduled, so "set" is exactly "will end later, not
    # yet" — and the legacy boolean is OR'd in for any account/flow that still
    # sends it.
    cancel_at = sub_obj.get("cancel_at")
    cancel_at_period_end = bool(sub_obj.get("cancel_at_period_end")) or cancel_at is not None
    now = datetime.now(UTC)

    with engine.begin() as connection:
        row = connection.execute(
            select(subscriptions.c.user_email).where(
                subscriptions.c.stripe_subscription_id == subscription_id
            )
        ).first()
        if row is None:
            # Out-of-order delivery: the `checkout.session.completed` that
            # creates our row hasn't landed yet. Nothing to update here — that
            # event inserts the row itself; a later update catches it up.
            log.warning("stripe_subscription_updated_unknown", subscription_id=subscription_id)
            return
        connection.execute(
            subscriptions.update()
            .where(subscriptions.c.stripe_subscription_id == subscription_id)
            .values(
                status=status,
                price_id=price_id or settings.stripe_price_id,
                current_period_end=(
                    datetime.fromtimestamp(period_end, tz=UTC) if period_end else None
                ),
                cancel_at_period_end=cancel_at_period_end,
                cancel_at=(datetime.fromtimestamp(cancel_at, tz=UTC) if cancel_at else None),
                updated_at=now,
            )
        )
        email = row.user_email

    set_user_tier(engine, email=email, tier=_tier_for_status(status))
    log.info(
        "stripe_subscription_updated",
        email=email,
        status=status,
        subscription_id=subscription_id,
        cancel_at_period_end=cancel_at_period_end,
        cancel_at=cancel_at,
        current_period_end=period_end,
    )


def _handle_subscription_deleted(engine: Engine, sub_obj: dict[str, Any]) -> None:
    """`customer.subscription.deleted` — the subscription is gone for good
    (as opposed to `past_due`, which `_tier_for_status` still treats as
    plus). Marks the row canceled and drops the buyer back to free.
    """
    subscription_id = sub_obj.get("id")
    now = datetime.now(UTC)

    with engine.begin() as connection:
        row = connection.execute(
            select(subscriptions.c.user_email).where(
                subscriptions.c.stripe_subscription_id == subscription_id
            )
        ).first()
        if row is None:
            log.warning("stripe_subscription_deleted_unknown", subscription_id=subscription_id)
            return
        connection.execute(
            subscriptions.update()
            .where(subscriptions.c.stripe_subscription_id == subscription_id)
            .values(status="canceled", updated_at=now)
        )
        email = row.user_email

    set_user_tier(engine, email=email, tier="free")
    log.info("stripe_subscription_canceled", email=email, subscription_id=subscription_id)


_EVENT_HANDLERS = {
    "checkout.session.completed": _handle_checkout_completed,
    "customer.subscription.updated": _handle_subscription_updated,
    "customer.subscription.deleted": _handle_subscription_deleted,
}


@router.post(
    "/webhook/stripe", summary="Stripe subscription/payment events", include_in_schema=False
)
async def stripe_webhook(request: Request) -> dict[str, bool]:
    """Verifies and applies Stripe's push notifications for this account's
    subscriptions — the other half of `start_checkout`, the part that actually
    flips `users.tier` once a card is charged, renews, or fails for good.

    Configure this URL (`<hub_base_url>/api/billing/webhook/stripe`) in the Stripe
    Dashboard's webhook settings, subscribed to at least
    `checkout.session.completed`, `customer.subscription.updated`, and
    `customer.subscription.deleted`. Unrecognized event types are accepted
    and logged, not rejected — Stripe's dashboard lets an account subscribe to
    more events than this handler acts on, and a 4xx/5xx there just triggers
    Stripe's retry schedule for no reason.
    """
    if not settings.stripe_webhook_secret:
        raise HTTPException(503, "The billing webhook is not configured on this deployment.")
    engine: Engine | None = getattr(request.app.state, "engine", None)
    if engine is None:
        # Stripe retries a non-2xx delivery on a backoff schedule, so 503
        # (rather than silently 200-ing and losing the event) is the honest
        # answer to "the database that would record this is down."
        raise HTTPException(503, "Billing storage is not available.")

    payload = await request.body()
    signature = request.headers.get("stripe-signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, signature, settings.stripe_webhook_secret)
    except (ValueError, stripe.SignatureVerificationError) as exc:
        log.warning("stripe_webhook_signature_invalid", error=str(exc))
        raise HTTPException(400, "Invalid Stripe signature.") from exc

    if not _record_event_once(engine, event_id=event["id"], event_type=event["type"]):
        log.info("stripe_webhook_duplicate_delivery", event_id=event["id"], type=event["type"])
        return {"received": True}

    handler = _EVENT_HANDLERS.get(event["type"])
    if handler is not None:
        # `event["data"]["object"]` is a `StripeObject` — subscriptable but
        # deliberately *not* a dict (it raises on `.get()`, to keep dict-typed
        # API params from silently accepting one). Handlers below want plain
        # `.get()` semantics for optional fields, so convert once, up front.
        handler(engine, event["data"]["object"].to_dict())
    else:
        log.info("stripe_webhook_ignored", type=event["type"])
    return {"received": True}
