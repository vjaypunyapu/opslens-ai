"""
OpsLens AI – Master Fan-Out Tasks
====================================
These are the top-level Celery Beat tasks.  Each one loads all active tenants
from the DB and dispatches per-tenant subtasks, keeping the Beat schedule
decoupled from individual tenant IDs.
"""
from __future__ import annotations

import sqlalchemy as sa
from celery import shared_task
from celery.utils.log import get_task_logger

from ..async_utils import run_async as _run_async
from ..db import AsyncSession
from ..models.tenant import Tenant

logger = get_task_logger(__name__)


async def _get_all_tenant_ids() -> list[str]:
    async with AsyncSession() as db:
        rows = await db.execute(sa.select(Tenant.id))
        return [str(r) for r in rows.scalars().all()]


# ── Insights fan-out ──────────────────────────────────────────────────────────
@shared_task(name="insights.run_all_tenants")
def run_all_tenants_insights():
    """Dispatch run_all_insights_for_tenant for every tenant in the DB."""
    from .insight_runner import run_all_insights_for_tenant

    tenant_ids = _run_async(_get_all_tenant_ids())
    logger.info("Dispatching insights for %d tenants", len(tenant_ids))
    for tid in tenant_ids:
        run_all_insights_for_tenant.delay(tid)
    return {"dispatched": len(tenant_ids)}


# ── Alerts fan-out ────────────────────────────────────────────────────────────
@shared_task(name="alerts.evaluate_all_tenants")
def evaluate_all_tenants_alerts():
    """Dispatch evaluate_alerts_for_tenant for every tenant."""
    from .alert_runner import evaluate_alerts_for_tenant

    tenant_ids = _run_async(_get_all_tenant_ids())
    logger.info("Dispatching alert evaluation for %d tenants", len(tenant_ids))
    for tid in tenant_ids:
        evaluate_alerts_for_tenant.delay(tid)
    return {"dispatched": len(tenant_ids)}


# ── Staging processor fan-out ─────────────────────────────────────────────────
@shared_task(name="ingestion.process_all_staging")
def process_all_staging():
    """
    For every tenant, dispatch process_staging_batch for each active source type.
    """
    from .ingestion import process_staging_batch, NORMALIZERS

    async def _get_tenant_integrations():
        from ..models.integration import Integration
        async with AsyncSession() as db:
            rows = await db.execute(
                sa.select(Integration.tenant_id, Integration.source_type)
                .where(Integration.status == "active")
            )
            return [(str(r.tenant_id), r.source_type) for r in rows.all()]

    pairs = _run_async(_get_tenant_integrations())
    logger.info("Dispatching staging batch for %d tenant-source pairs", len(pairs))
    dispatched = 0
    for tenant_id, source_type in pairs:
        if source_type in NORMALIZERS:
            process_staging_batch.delay(tenant_id, source_type)
            dispatched += 1
    return {"dispatched": dispatched}
