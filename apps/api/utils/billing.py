"""
OpsLens AI — Usage Recording Helper
======================================
Call record_usage() from any endpoint or task to emit a UsageEvent and
increment the pending counters on BillingSubscription in a single DB round-trip.

Usage:
    from apps.api.utils.billing import record_usage

    await record_usage(
        db,
        tenant_id=ctx.tenant_id,
        event_type="rag_query",
        actor_id=ctx.user_id,
        resource_id=session_id,
        quantity=1,
        metadata={"model": "gpt-4o", "tokens": 1420},
    )

event_type values:
    doc_ingested        quantity = chunk count
    alert_fired         quantity = 1
    brief_generated     quantity = 1
    rag_query           quantity = 1
    simulation_run      quantity = 1
    webhook_ingest      quantity = 1
    seat_active         quantity = 1 (daily heartbeat)
"""
from __future__ import annotations

import logging
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Cost per unit (USD) — update as OpenAI pricing changes
UNIT_COSTS: dict[str, Decimal] = {
    "doc_ingested":     Decimal("0.0008"),   # ~$0.0008 per chunk (embedding + HyDE)
    "alert_fired":      Decimal("0.02"),     # enrichment LLM call
    "brief_generated":  Decimal("0.04"),     # full RRT brief LLM call
    "rag_query":        Decimal("0.015"),    # planner + retrieve + validate
    "simulation_run":   Decimal("0.02"),
    "webhook_ingest":   Decimal("0.02"),
    "seat_active":      Decimal("0"),
}

# Pending counter column names on BillingSubscription
PENDING_FIELD: dict[str, str] = {
    "doc_ingested":     "pending_docs",
    "alert_fired":      "pending_alerts",
    "brief_generated":  "pending_briefs",
    "rag_query":        "pending_rag_queries",
    "simulation_run":   "pending_alerts",
    "webhook_ingest":   "pending_alerts",
}


async def record_usage(
    db: AsyncSession,
    *,
    tenant_id: str,
    event_type: str,
    actor_id: str | None = None,
    resource_id: str | None = None,
    quantity: int = 1,
    metadata: dict[str, Any] | None = None,
) -> None:
    """
    Write one UsageEvent row and increment the pending counter on
    BillingSubscription. Never raises — silently logs on failure so
    a billing write never blocks the actual operation.
    """
    try:
        import sqlalchemy as sa
        from apps.api.models.billing import BillingSubscription, UsageEvent

        unit_cost  = UNIT_COSTS.get(event_type, Decimal("0"))
        total_cost = unit_cost * quantity

        event = UsageEvent(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            event_type=event_type,
            resource_id=resource_id,
            actor_id=actor_id or "system",
            quantity=quantity,
            unit_cost_usd=unit_cost,
            total_cost_usd=total_cost,
            extra=metadata,
        )
        db.add(event)

        # Increment pending counter (upsert-style: update if row exists)
        pending_field = PENDING_FIELD.get(event_type)
        if pending_field:
            await db.execute(
                sa.text(f"""
                    UPDATE opslens.billing_subscriptions
                    SET {pending_field} = {pending_field} + :qty,
                        updated_at = now()
                    WHERE tenant_id = :tid
                """),
                {"qty": quantity, "tid": tenant_id},
            )

        # Intentionally no commit here — caller manages the transaction
    except Exception:
        logger.exception(
            "record_usage failed (non-fatal): tenant=%s event=%s",
            tenant_id, event_type,
        )
