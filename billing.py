"""Subscriptions, plan limits and usage metering.

The market prices this category as flat monthly tiers with unlimited seats,
gated on volume. That structure is copied here deliberately, with one change
that matters: the tiers meter *claims*, not seats.

Seats are free to serve. A claim is not. Every item on a claim costs research
calls against the Anthropic API, and a 99-item claim costs that many times
over. An unlimited-claims plan would make the highest-volume customer the
least profitable one, so each tier carries an allowance and overage is
charged per claim beyond it.

IMPORTANT — the allowances and prices below are placeholders positioned
against the market, not against measured cost. Before publishing them, run
real claims with usage logging on and confirm the worst-case research spend on
each tier still clears margin. `cost_note()` exists to make that check.

Stripe is optional at import time: with no key configured the app runs in a
local mode where plans can be set directly, so the rest of the product is
testable without touching a payment processor.
"""
from __future__ import annotations

import datetime as dt
import os

try:  # optional dependency; absent in local development
    import stripe
except Exception:  # noqa: BLE001
    stripe = None

STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")

if stripe and STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

TRIAL_DAYS = 14

# Prices are AUD, quoted ex-GST. Stripe Tax adds the 10% and issues the tax
# invoice; do not bake GST into these numbers.
PLANS: dict[str, dict] = {
    "trial": {
        "name": "Trial",
        "price": 0,
        "claims": 3,
        "blurb": f"{TRIAL_DAYS} days, three claims, every feature.",
        "price_id_env": "",
    },
    "small": {
        "name": "Small practice",
        "price": 270,
        "claims": 25,
        "blurb": "For a sole assessor or a small team.",
        "price_id_env": "STRIPE_PRICE_SMALL",
    },
    "medium": {
        "name": "Practice",
        "price": 455,
        "claims": 75,
        "blurb": "For a growing assessing practice.",
        "price_id_env": "STRIPE_PRICE_MEDIUM",
    },
    "large": {
        "name": "Firm",
        "price": 650,
        "claims": 200,
        "blurb": "For a firm running catastrophe volume.",
        "price_id_env": "STRIPE_PRICE_LARGE",
    },
}

OVERAGE_PRICE = 18  # AUD per claim beyond the allowance, ex-GST

PAID_PLANS = [k for k in PLANS if k != "trial"]


def configured() -> bool:
    """True when Stripe can actually be called."""
    return bool(stripe and STRIPE_SECRET_KEY)


def local_mode_allowed() -> bool:
    """Whether a plan may be applied without taking payment.

    This exists so the product is testable without a payment processor. On a
    public deployment it is a hole you could drive a truck through: anyone who
    signs up could put themselves on the top plan for nothing. It is therefore
    off unless explicitly switched on, and it can never be on at the same time
    as real Stripe keys.
    """
    return not configured() and os.environ.get("ALLOW_LOCAL_BILLING", "") == "1"


def price_id(plan: str) -> str:
    env = PLANS.get(plan, {}).get("price_id_env", "")
    return os.environ.get(env, "") if env else ""


def cost_note(items_valued: int, searches_per_item: int | None = None) -> str:
    """A reminder, not a calculation.

    Actual research cost per claim is not knowable from here — it depends on
    the model, the search count and how many items are re-valued. Instrument
    valuation.py and measure it before trusting the allowances above.
    """
    searches = searches_per_item or int(os.environ.get("MAX_SEARCHES_PER_ITEM", "3"))
    return (
        f"{items_valued} items x up to {searches} searches. "
        "Confirm measured cost per claim before publishing these tiers."
    )


# ------------------------------------------------------------------ metering


def start_trial(org) -> None:
    now = dt.datetime.now(dt.timezone.utc)
    org.plan = "trial"
    org.plan_status = "trialing"
    org.period_start = now
    org.claims_used = 0
    org.trial_ends_at = now + dt.timedelta(days=TRIAL_DAYS)


def _period_elapsed(org) -> bool:
    start = org.period_start
    if start is None:
        return True
    if start.tzinfo is None:
        start = start.replace(tzinfo=dt.timezone.utc)
    return (dt.datetime.now(dt.timezone.utc) - start).days >= 30


def roll_period_if_due(org) -> bool:
    """Reset the claim counter once a billing month has elapsed.

    Stripe's invoice.paid webhook is the authority when Stripe is wired up;
    this keeps the counter honest in local mode and covers a missed webhook.
    """
    if org.plan_status not in ("active", "trialing"):
        return False
    if not _period_elapsed(org):
        return False
    org.period_start = dt.datetime.now(dt.timezone.utc)
    org.claims_used = 0
    return True


def trial_expired(org) -> bool:
    if org.plan != "trial" or not org.trial_ends_at:
        return False
    ends = org.trial_ends_at
    if ends.tzinfo is None:
        ends = ends.replace(tzinfo=dt.timezone.utc)
    return ends < dt.datetime.now(dt.timezone.utc)


def gate(org) -> str:
    """Why this organisation cannot start a claim, or an empty string."""
    if trial_expired(org):
        return "Your trial has ended. Choose a plan to keep assessing claims."
    if org.plan_status == "past_due":
        return "The last payment failed. Update the card to keep assessing claims."
    if org.plan_status == "cancelled":
        return "This subscription has been cancelled. Choose a plan to continue."
    if org.claims_remaining <= 0:
        allowance = org.claim_allowance
        return (
            f"This month's allowance of {allowance} claims is used. "
            f"Move up a plan, or continue at ${OVERAGE_PRICE} per claim."
        )
    return ""


def count_claim(org) -> None:
    org.claims_used = (org.claims_used or 0) + 1


# -------------------------------------------------------------------- stripe


def checkout_url(org, plan: str, email: str, base_url: str) -> str:
    """A Stripe Checkout session for a subscription, or '' in local mode."""
    if not configured():
        return ""
    pid = price_id(plan)
    if not pid:
        raise RuntimeError(
            f"No Stripe price configured for '{plan}'. "
            f"Set {PLANS[plan]['price_id_env']}."
        )
    root = PUBLIC_BASE_URL or base_url.rstrip("/")
    session = stripe.checkout.Session.create(
        mode="subscription",
        customer=org.stripe_customer_id or None,
        customer_email=None if org.stripe_customer_id else email,
        line_items=[{"price": pid, "quantity": 1}],
        client_reference_id=org.id,
        subscription_data={"metadata": {"organisation_id": org.id, "plan": plan}},
        metadata={"organisation_id": org.id, "plan": plan},
        automatic_tax={"enabled": True},  # GST
        success_url=f"{root}/settings/billing?checkout=success",
        cancel_url=f"{root}/settings/billing?checkout=cancelled",
    )
    return session.url


def portal_url(org, base_url: str) -> str:
    """Stripe's own billing portal: card changes, invoices, cancellation."""
    if not configured() or not org.stripe_customer_id:
        return ""
    root = PUBLIC_BASE_URL or base_url.rstrip("/")
    session = stripe.billing_portal.Session.create(
        customer=org.stripe_customer_id,
        return_url=f"{root}/settings/billing",
    )
    return session.url


def verify_webhook(payload: bytes, signature: str):
    """Verify and parse a Stripe webhook. Raises if the signature is wrong."""
    if not stripe:
        raise RuntimeError("stripe library is not installed")
    if not STRIPE_WEBHOOK_SECRET:
        raise RuntimeError("STRIPE_WEBHOOK_SECRET is not set")
    return stripe.Webhook.construct_event(payload, signature, STRIPE_WEBHOOK_SECRET)


def apply_event(event, org_by_id, org_by_customer) -> str:
    """Fold a Stripe event into the organisation. Returns what happened.

    The caller supplies lookups and owns the commit, so this stays testable
    without a database or a Stripe account.
    """
    kind = event["type"]
    obj = event["data"]["object"]

    def resolve(o):
        oid = (o.get("metadata") or {}).get("organisation_id") or o.get("client_reference_id")
        if oid:
            found = org_by_id(oid)
            if found:
                return found
        customer = o.get("customer")
        return org_by_customer(customer) if customer else None

    org = resolve(obj)
    if org is None:
        return f"ignored {kind}: no matching organisation"

    if kind == "checkout.session.completed":
        org.stripe_customer_id = obj.get("customer") or org.stripe_customer_id
        org.stripe_subscription_id = obj.get("subscription") or org.stripe_subscription_id
        plan = (obj.get("metadata") or {}).get("plan")
        if plan in PLANS:
            org.plan = plan
        org.plan_status = "active"
        org.period_start = dt.datetime.now(dt.timezone.utc)
        org.claims_used = 0
        return f"activated {org.plan}"

    if kind in ("customer.subscription.created", "customer.subscription.updated"):
        org.stripe_subscription_id = obj.get("id") or org.stripe_subscription_id
        plan = (obj.get("metadata") or {}).get("plan")
        if plan in PLANS:
            org.plan = plan
        status = obj.get("status")
        org.plan_status = {
            "active": "active", "trialing": "trialing", "past_due": "past_due",
            "unpaid": "past_due", "canceled": "cancelled", "incomplete_expired": "cancelled",
        }.get(status, org.plan_status)
        return f"subscription {status}"

    if kind == "customer.subscription.deleted":
        org.plan_status = "cancelled"
        return "subscription cancelled"

    if kind == "invoice.paid":
        # A new billing month: the allowance resets here, not on a timer.
        org.plan_status = "active"
        org.period_start = dt.datetime.now(dt.timezone.utc)
        org.claims_used = 0
        return "period rolled"

    if kind == "invoice.payment_failed":
        org.plan_status = "past_due"
        return "payment failed"

    return f"ignored {kind}"
