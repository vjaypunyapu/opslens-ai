"""
OpsLens AI — Billing & Metering Router
=========================================
  GET  /api/v1/billing/usage              — Current period usage summary
  GET  /api/v1/billing/subscription       — Subscription plan + limits
  POST /api/v1/billing/subscription       — Create / update subscription (admin)
  GET  /api/v1/billing/events             — Raw usage event log (admin)
  POST /api/v1/billing/stripe/webhook     — Stripe webhook handler (no auth — verified by sig)
  POST /api/v1/billing/report             — Manually push pending usage to Stripe (admin)

Usage events are written by a shared helper:
    from apps.api.utils.billing import record_usage
    await record_usage(db, tenant_id=ctx.tenant_id, event_type="rag_query", actor_id=ctx.user_id)

This is called from rag.py, log_ops.py, rrt_briefing, etc.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from ..auth.dependencies import TenantContext, require_admin, require_viewer
from ..db.session import get_db
from ..models.billing import BillingSubscription, UsageEvent

router = APIRouter()
logger = logging.getLogger(__name__)

# ── Plan definitions ──────────────────────────────────────────────────────────

PLANS = {
    "free": {
        "seats_limit":          3,
        "docs_limit_per_month": 500,
        "alerts_limit_per_month": 100,
        "price_usd_per_month":  0,
        "features": ["Core alert detection", "5-min polling", "Basic RAG", "Slack notifications"],
    },
    "starter": {
        "seats_limit":          10,
        "docs_limit_per_month": 5_000,
        "alerts_limit_per_month": 1_000,
        "price_usd_per_month":  99,
        "features": ["Everything in Free", "Webhook ingest", "RBAC teams", "Jira comment ingestion",
                     "Priority support"],
    },
    "growth": {
        "seats_limit":          50,
        "docs_limit_per_month": 50_000,
        "alerts_limit_per_month": 10_000,
        "price_usd_per_month":  399,
        "features": ["Everything in Starter", "SAML SSO", "SCIM provisioning", "Audit log export",
                     "Retention policies", "Dedicated Slack channel"],
    },
    "enterprise": {
        "seats_limit":          0,       # unlimited
        "docs_limit_per_month": 0,       # unlimited
        "alerts_limit_per_month": 0,     # unlimited
        "price_usd_per_month":  None,    # custom
        "features": ["Everything in Growth", "Self-hosted option", "Custom data residency",
                     "SLA guarantee", "Dedicated CSM", "Custom contract"],
    },
}


# ── Schemas ───────────────────────────────────────────────────────────────────

class SubscriptionIn(BaseModel):
    plan:                   str = Field(..., description="free | starter | growth | enterprise")
    stripe_customer_id:     str | None = None
    stripe_subscription_id: str | None = None


class UsageSummaryOut(BaseModel):
    tenant_id:              str
    plan:                   str
    period_start:           str
    period_end:             str
    docs_ingested:          int
    docs_limit:             int
    alerts_fired:           int
    alerts_limit:           int
    briefs_generated:       int
    rag_queries:            int
    total_cost_usd:         float
    over_limit:             bool
    days_remaining:         int


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_or_create_subscription(tenant_id: str, db) -> BillingSubscription:
    result = await db.execute(
        sa.select(BillingSubscription).where(BillingSubscription.tenant_id == tenant_id)
    )
    sub = result.scalar_one_or_none()
    if not sub:
        sub = BillingSubscription(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            plan="free",
            status="trialing",
            trial_ends_at=datetime.now(tz=timezone.utc) + timedelta(days=14),
        )
        db.add(sub)
        await db.commit()
        await db.refresh(sub)
    return sub


def _period_bounds(sub: BillingSubscription) -> tuple[datetime, datetime]:
    """Return (start, end) of the current billing period."""
    now = datetime.now(tz=timezone.utc)
    if sub.current_period_start and sub.current_period_end:
        return sub.current_period_start, sub.current_period_end
    # Default: calendar month
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if now.month == 12:
        end = start.replace(year=now.year + 1, month=1)
    else:
        end = start.replace(month=now.month + 1)
    return start, end


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/usage", response_model=UsageSummaryOut)
async def get_usage_summary(
    ctx: Annotated[TenantContext, Depends(require_viewer)],
    db=Depends(get_db),
):
    """Current billing period usage vs. plan limits."""
    sub = await _get_or_create_subscription(ctx.tenant_id, db)
    period_start, period_end = _period_bounds(sub)
    plan_def = PLANS.get(sub.plan, PLANS["free"])

    # Aggregate usage events for this period
    events_result = await db.execute(
        sa.select(
            UsageEvent.event_type,
            sa.func.sum(UsageEvent.quantity).label("qty"),
            sa.func.sum(UsageEvent.total_cost_usd).label("cost"),
        )
        .where(
            UsageEvent.tenant_id == ctx.tenant_id,
            UsageEvent.occurred_at >= period_start,
            UsageEvent.occurred_at < period_end,
        )
        .group_by(UsageEvent.event_type)
    )
    rows = {r.event_type: {"qty": int(r.qty or 0), "cost": float(r.cost or 0)}
            for r in events_result.fetchall()}

    docs_ingested   = rows.get("doc_ingested", {}).get("qty", 0)
    alerts_fired    = rows.get("alert_fired",  {}).get("qty", 0)
    briefs_generated = rows.get("brief_generated", {}).get("qty", 0)
    rag_queries     = rows.get("rag_query",    {}).get("qty", 0)
    total_cost      = sum(v["cost"] for v in rows.values())

    docs_limit   = plan_def["docs_limit_per_month"]
    alerts_limit = plan_def["alerts_limit_per_month"]

    over_limit = (
        (docs_limit > 0 and docs_ingested > docs_limit) or
        (alerts_limit > 0 and alerts_fired > alerts_limit)
    )
    days_remaining = max(0, (period_end - datetime.now(tz=timezone.utc)).days)

    return UsageSummaryOut(
        tenant_id=ctx.tenant_id,
        plan=sub.plan,
        period_start=period_start.isoformat(),
        period_end=period_end.isoformat(),
        docs_ingested=docs_ingested,
        docs_limit=docs_limit,
        alerts_fired=alerts_fired,
        alerts_limit=alerts_limit,
        briefs_generated=briefs_generated,
        rag_queries=rag_queries,
        total_cost_usd=round(total_cost, 4),
        over_limit=over_limit,
        days_remaining=days_remaining,
    )


@router.get("/subscription")
async def get_subscription(
    ctx: Annotated[TenantContext, Depends(require_viewer)],
    db=Depends(get_db),
):
    """Current subscription plan, limits, and Stripe status."""
    sub = await _get_or_create_subscription(ctx.tenant_id, db)
    plan_def = PLANS.get(sub.plan, PLANS["free"])
    return {
        "tenant_id":            ctx.tenant_id,
        "plan":                 sub.plan,
        "status":               sub.status,
        "trial_ends_at":        sub.trial_ends_at.isoformat() if sub.trial_ends_at else None,
        "current_period_start": sub.current_period_start.isoformat() if sub.current_period_start else None,
        "current_period_end":   sub.current_period_end.isoformat() if sub.current_period_end else None,
        "limits": {
            "seats":              plan_def["seats_limit"],
            "docs_per_month":     plan_def["docs_limit_per_month"],
            "alerts_per_month":   plan_def["alerts_limit_per_month"],
        },
        "features":  plan_def["features"],
        "price_usd": plan_def["price_usd_per_month"],
        "stripe_customer_id": sub.stripe_customer_id,
    }


@router.post("/subscription", status_code=200)
async def upsert_subscription(
    body: SubscriptionIn,
    ctx: Annotated[TenantContext, Depends(require_admin)],
    db=Depends(get_db),
):
    """Create or update the subscription plan (admin only)."""
    if body.plan not in PLANS:
        raise HTTPException(status_code=400, detail=f"Invalid plan. Choose: {list(PLANS)}")
    sub = await _get_or_create_subscription(ctx.tenant_id, db)
    plan_def = PLANS[body.plan]
    sub.plan = body.plan
    sub.seats_limit = plan_def["seats_limit"]
    sub.docs_limit_per_month = plan_def["docs_limit_per_month"]
    sub.alerts_limit_per_month = plan_def["alerts_limit_per_month"]
    if body.stripe_customer_id:
        sub.stripe_customer_id = body.stripe_customer_id
    if body.stripe_subscription_id:
        sub.stripe_subscription_id = body.stripe_subscription_id
        sub.status = "active"
    sub.updated_at = datetime.now(tz=timezone.utc)
    await db.commit()
    return {"status": "updated", "plan": sub.plan}


@router.get("/events")
async def list_usage_events(
    ctx: Annotated[TenantContext, Depends(require_admin)],
    db=Depends(get_db),
    event_type: str | None = Query(None),
    limit: int = Query(default=100, le=500),
    since: datetime | None = Query(None),
):
    """Raw usage event log — useful for debugging and billing reconciliation."""
    filters = [UsageEvent.tenant_id == ctx.tenant_id]
    if event_type:
        filters.append(UsageEvent.event_type == event_type)
    if since:
        filters.append(UsageEvent.occurred_at >= since)

    result = await db.execute(
        sa.select(UsageEvent)
        .where(*filters)
        .order_by(UsageEvent.occurred_at.desc())
        .limit(limit)
    )
    events = result.scalars().all()
    return [
        {
            "id":             e.id,
            "event_type":     e.event_type,
            "quantity":       e.quantity,
            "total_cost_usd": float(e.total_cost_usd),
            "actor_id":       e.actor_id,
            "resource_id":    e.resource_id,
            "occurred_at":    e.occurred_at.isoformat(),
            "metadata":       e.metadata,
        }
        for e in events
    ]


@router.post("/report", status_code=200)
async def report_usage_to_stripe(
    ctx: Annotated[TenantContext, Depends(require_admin)],
    db=Depends(get_db),
):
    """
    Push pending usage counters to Stripe Billing Meter API.
    Called automatically by Celery Beat daily; also triggerable manually.
    """
    from apps.api.config import settings as cfg

    sub = await _get_or_create_subscription(ctx.tenant_id, db)
    if not sub.stripe_subscription_id:
        return {"status": "skipped", "reason": "No Stripe subscription linked"}

    stripe_key = getattr(cfg, "STRIPE_SECRET_KEY", None)
    if not stripe_key:
        return {"status": "skipped", "reason": "STRIPE_SECRET_KEY not configured"}

    reported: dict[str, int] = {}
    errors: list[str] = []

    meter_map = {
        "doc_ingested":   getattr(cfg, "STRIPE_METER_DOCS",   None),
        "alert_fired":    getattr(cfg, "STRIPE_METER_ALERTS", None),
        "brief_generated":getattr(cfg, "STRIPE_METER_BRIEFS", None),
        "rag_query":      getattr(cfg, "STRIPE_METER_QUERIES", None),
    }

    try:
        import httpx
        for event_type, meter_id in meter_map.items():
            if not meter_id:
                continue
            qty = getattr(sub, f"pending_{event_type.replace('_fired','_alerts').replace('_ingested','_docs').replace('_generated','_briefs').replace('rag_query','rag_queries')}", 0)
            if qty <= 0:
                continue
            resp = httpx.post(
                f"https://api.stripe.com/v1/billing/meter_events",
                auth=(stripe_key, ""),
                data={
                    "event_name":   meter_id,
                    "payload[stripe_customer_id]": sub.stripe_customer_id,
                    "payload[value]": str(qty),
                },
                timeout=10,
            )
            if resp.status_code == 200:
                reported[event_type] = qty
            else:
                errors.append(f"{event_type}: HTTP {resp.status_code} {resp.text[:100]}")

        if reported:
            # Zero out the pending counters
            await db.execute(
                sa.update(BillingSubscription)
                .where(BillingSubscription.tenant_id == ctx.tenant_id)
                .values(
                    pending_docs=0,
                    pending_alerts=0,
                    pending_briefs=0,
                    pending_rag_queries=0,
                    updated_at=datetime.now(tz=timezone.utc),
                )
            )
            await db.commit()

    except Exception as exc:
        errors.append(str(exc))

    return {"status": "ok" if not errors else "partial", "reported": reported, "errors": errors}


@router.post("/stripe/webhook", status_code=200)
async def stripe_webhook(
    request: Request,
    db=Depends(get_db),
    stripe_signature: str | None = Header(None, alias="stripe-signature"),
):
    """
    Stripe webhook handler — listens for subscription lifecycle events.
    Endpoint must be configured in Stripe Dashboard → Webhooks.

    Events handled:
        customer.subscription.created
        customer.subscription.updated
        customer.subscription.deleted
        invoice.payment_succeeded
        invoice.payment_failed
    """
    from apps.api.config import settings as cfg

    webhook_secret = getattr(cfg, "STRIPE_WEBHOOK_SECRET", None)
    body_bytes = await request.body()

    # Verify Stripe signature
    if webhook_secret and stripe_signature:
        try:
            _verify_stripe_signature(body_bytes, stripe_signature, webhook_secret)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Webhook signature invalid: {exc}")

    import json
    try:
        event = json.loads(body_bytes)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    event_type = event.get("type", "")
    data = event.get("data", {}).get("object", {})

    if event_type in ("customer.subscription.created", "customer.subscription.updated"):
        customer_id = data.get("customer")
        sub_id = data.get("id")
        stripe_status = data.get("status", "active")
        period_start = datetime.fromtimestamp(data.get("current_period_start", 0), tz=timezone.utc)
        period_end   = datetime.fromtimestamp(data.get("current_period_end",   0), tz=timezone.utc)

        # Find tenant by stripe_customer_id
        result = await db.execute(
            sa.select(BillingSubscription).where(
                BillingSubscription.stripe_customer_id == customer_id
            )
        )
        sub = result.scalar_one_or_none()
        if sub:
            sub.stripe_subscription_id = sub_id
            sub.status = stripe_status
            sub.current_period_start = period_start
            sub.current_period_end   = period_end
            sub.updated_at = datetime.now(tz=timezone.utc)
            await db.commit()
            logger.info("Stripe subscription updated: customer=%s status=%s", customer_id, stripe_status)

    elif event_type == "customer.subscription.deleted":
        customer_id = data.get("customer")
        result = await db.execute(
            sa.select(BillingSubscription).where(
                BillingSubscription.stripe_customer_id == customer_id
            )
        )
        sub = result.scalar_one_or_none()
        if sub:
            sub.status = "canceled"
            sub.canceled_at = datetime.now(tz=timezone.utc)
            sub.plan = "free"
            await db.commit()
            logger.info("Stripe subscription canceled: customer=%s", customer_id)

    elif event_type == "invoice.payment_failed":
        customer_id = data.get("customer")
        result = await db.execute(
            sa.select(BillingSubscription).where(
                BillingSubscription.stripe_customer_id == customer_id
            )
        )
        sub = result.scalar_one_or_none()
        if sub:
            sub.status = "past_due"
            await db.commit()
            logger.warning("Stripe payment failed: customer=%s", customer_id)

    return {"received": True, "event_type": event_type}


def _verify_stripe_signature(payload: bytes, sig_header: str, secret: str) -> None:
    """Verify Stripe webhook signature (Stripe-Signature header)."""
    try:
        parts = {k: v for k, v in (item.split("=", 1) for item in sig_header.split(","))}
        timestamp = parts.get("t", "")
        v1_sig    = parts.get("v1", "")
        signed_payload = f"{timestamp}.".encode() + payload
        expected = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, v1_sig):
            raise ValueError("Signature mismatch")
    except (KeyError, ValueError) as exc:
        raise ValueError(f"Stripe signature verification failed: {exc}") from exc
