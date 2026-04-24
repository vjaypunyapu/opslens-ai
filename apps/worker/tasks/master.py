"""
OpsLens AI – Master Fan-Out Tasks
====================================
These are the top-level Celery Beat tasks.  Each one loads all active tenants
from the DB and dispatches per-tenant subtasks, keeping the Beat schedule
decoupled from individual tenant IDs.
"""
from __future__ import annotations

import random
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


# ── Log source polling fan-out ────────────────────────────────────────────────
@shared_task(name="logs.poll_all_log_sources")
def poll_all_log_sources():
    """
    Periodically polls every active log-source integration (Elasticsearch,
    Datadog, CloudWatch, GCP, Splunk, Azure Monitor) across all tenants.

    This is the zero-config pull path — customers connect once via the UI
    and OpsLens handles all polling automatically. No Airbyte, no SDK, no
    webhook setup required on the customer's side.

    Runs every 5 minutes via Celery Beat (same cadence as fast_scan).
    Each integration is dispatched as an independent subtask so one slow
    source doesn't block others.
    """
    from .log_source_poller import poll_log_source

    async def _get_active_log_integrations():
        from ..models.integration import Integration
        from ..services.direct_sync_service import LOG_SOURCE_TYPES
        async with AsyncSession() as db:
            rows = await db.execute(
                sa.select(Integration.id, Integration.tenant_id, Integration.source_type)
                .where(
                    Integration.status == "active",
                    Integration.source_type.in_(list(LOG_SOURCE_TYPES)),
                )
            )
            return [(str(r.id), str(r.tenant_id), r.source_type) for r in rows.all()]

    integrations = _run_async(_get_active_log_integrations())
    logger.info("Dispatching log source poll for %d active integrations", len(integrations))
    for integration_id, tenant_id, source_type in integrations:
        # Spread dispatches across the first 60 s of each 5-minute window to
        # avoid hammering all external APIs simultaneously (thundering herd).
        jitter = random.randint(0, 60)
        poll_log_source.apply_async(
            args=[integration_id, tenant_id, source_type],
            countdown=jitter,
        )
    return {"dispatched": len(integrations)}


# ── Contextual source daily re-sync fan-out ───────────────────────────────────
@shared_task(name="ingestion.sync_all_contextual_sources")
def sync_all_contextual_sources():
    """
    Re-sync all contextual integrations (GitHub, Jira, Slack, HubSpot, Zendesk,
    Google Drive, Bitbucket) across every tenant on the configured cron schedule.

    Each integration is dispatched as an independent sync_integration subtask so
    a slow or failing source doesn't block others. Jitter is applied across the
    first 10 minutes to avoid thundering-herd on external APIs.

    Schedule is configurable via env vars:
        CONTEXTUAL_SYNC_CRON_HOUR   (default "3"  → 03:xx UTC)
        CONTEXTUAL_SYNC_CRON_MINUTE (default "0"  → xx:00 UTC)
    """
    from .log_source_poller import sync_integration
    from ..services.direct_sync_service import LOG_SOURCE_TYPES

    async def _get_contextual_integrations():
        from ..models.integration import Integration
        async with AsyncSession() as db:
            rows = await db.execute(
                sa.select(Integration.id, Integration.tenant_id, Integration.source_type)
                .where(
                    Integration.status == "active",
                    Integration.source_type.notin_(list(LOG_SOURCE_TYPES)),
                )
            )
            return [(str(r.id), str(r.tenant_id), r.source_type) for r in rows.all()]

    integrations = _run_async(_get_contextual_integrations())
    logger.info(
        "sync_all_contextual_sources: dispatching %d integrations", len(integrations)
    )
    for integration_id, tenant_id, source_type in integrations:
        # Spread over 10 min so all tenants don't hit GitHub/Jira at the same second
        jitter = random.randint(0, 600)
        sync_integration.apply_async(
            args=[integration_id, tenant_id],
            countdown=jitter,
        )
        logger.info(
            "sync_all_contextual_sources: queued %s/%s (jitter=%ds)",
            source_type, integration_id, jitter,
        )
    return {"dispatched": len(integrations)}


# ── Fast log alert fan-out ────────────────────────────────────────────────────
@shared_task(name="logs.fast_scan_all_tenants")
def fast_scan_all_tenants():
    """Dispatch fast_scan for every tenant so routing rules are resolved per tenant."""
    from .log_fast_alert import fast_scan

    tenant_ids = _run_async(_get_all_tenant_ids())
    logger.info("Dispatching fast_scan for %d tenants", len(tenant_ids))
    for tid in tenant_ids:
        fast_scan.delay(tid)
    return {"dispatched": len(tenant_ids)}
