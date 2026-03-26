"""
OpsLens AI — Billing & Metering Models
========================================
Tracks every billable action so we can report usage to Stripe
and produce per-tenant invoices.

UsageEvent  — one row per billable action (append-only)
    event_type:
        doc_ingested        — one canonical document embedded into Qdrant
        alert_fired         — one Tier-2 enriched alert dispatched
        brief_generated     — one RRT brief created
        rag_query           — one RAG / chat query answered
        simulation_run      — one /simulate call
        webhook_ingest      — one /ingest call
        seat_active         — daily heartbeat per active user (for per-seat billing)

BillingSubscription — Stripe subscription state per tenant
    plan:   free | starter | growth | enterprise
    status: trialing | active | past_due | canceled
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB

from apps.api.db.base import Base


class UsageEvent(Base):
    """
    Append-only ledger of every billable action.

    Never updated or deleted (except by retention policy after billing period closes).
    This table is the source of truth for usage-based billing.
    """
    __tablename__ = "usage_events"
    __table_args__ = {"schema": "opslens"}

    id              = Column(String, primary_key=True)
    tenant_id       = Column(String, nullable=False, index=True)

    # What happened
    event_type      = Column(String, nullable=False, index=True)
    # e.g. doc_ingested | alert_fired | brief_generated | rag_query
    # | simulation_run | webhook_ingest | seat_active

    # Optional context (used for debugging, not billing logic)
    resource_id     = Column(String, nullable=True)   # doc_id, brief_id, etc.
    actor_id        = Column(String, nullable=True)   # user_id or "system"

    # Quantity — most events are 1, but doc_ingested carries chunk_count
    quantity        = Column(Integer, nullable=False, default=1)

    # Cost snapshot at time of event (USD, 6 decimal places)
    # Pre-computed so monthly totals are a simple SUM()
    unit_cost_usd   = Column(Numeric(12, 6), nullable=False, default=0)
    total_cost_usd  = Column(Numeric(12, 6), nullable=False, default=0)

    # Stripe metering fields — set after successful report to Stripe
    stripe_meter_event_id   = Column(String, nullable=True)
    stripe_reported_at      = Column(DateTime(timezone=True), nullable=True)

    # Immutable timestamp
    occurred_at     = Column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
        default=lambda: datetime.now(tz=timezone.utc),
    )

    # Free-form extras (model used, token count, etc.)
    metadata        = Column(JSONB, nullable=True)


class BillingSubscription(Base):
    """
    Mirrors the Stripe subscription state for a tenant.
    Updated by the Stripe webhook handler on every subscription.* event.
    """
    __tablename__ = "billing_subscriptions"
    __table_args__ = {"schema": "opslens"}

    id                      = Column(String, primary_key=True)
    tenant_id               = Column(String, nullable=False, unique=True, index=True)

    # Stripe IDs
    stripe_customer_id      = Column(String, nullable=True, index=True)
    stripe_subscription_id  = Column(String, nullable=True, index=True)

    # Plan
    plan                    = Column(String, nullable=False, default="free")
    # free | starter | growth | enterprise

    # Limits (0 = unlimited)
    seats_limit             = Column(Integer, nullable=False, default=3)
    docs_limit_per_month    = Column(Integer, nullable=False, default=500)
    alerts_limit_per_month  = Column(Integer, nullable=False, default=100)

    # Stripe subscription state
    status                  = Column(String, nullable=False, default="trialing")
    # trialing | active | past_due | canceled | unpaid

    trial_ends_at           = Column(DateTime(timezone=True), nullable=True)
    current_period_start    = Column(DateTime(timezone=True), nullable=True)
    current_period_end      = Column(DateTime(timezone=True), nullable=True)
    canceled_at             = Column(DateTime(timezone=True), nullable=True)

    # Usage-based billing: accumulated since last Stripe meter report
    pending_docs            = Column(Integer, nullable=False, default=0)
    pending_alerts          = Column(Integer, nullable=False, default=0)
    pending_briefs          = Column(Integer, nullable=False, default=0)
    pending_rag_queries     = Column(Integer, nullable=False, default=0)

    # Tracking
    created_at              = Column(DateTime(timezone=True), nullable=False,
                                     default=lambda: datetime.now(tz=timezone.utc))
    updated_at              = Column(DateTime(timezone=True), nullable=False,
                                     default=lambda: datetime.now(tz=timezone.utc),
                                     onupdate=lambda: datetime.now(tz=timezone.utc))
