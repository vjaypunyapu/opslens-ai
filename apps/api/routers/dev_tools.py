"""
OpsLens AI — Demo / Dev Tools
==============================
Endpoints for triggering REAL errors and forcing log syncs so you can
demo the full alert → RAG → RRT brief pipeline without waiting for the
scheduler or manufacturing artificial conditions.

All endpoints require admin auth. Do NOT expose in production without
restricting access — these intentionally generate noisy logs.

Endpoints:
    POST /dev/crash        — Write a real traceback to stdout N times so
                             Railway captures it; OpsLens polls and alerts.
    POST /dev/force-sync   — Immediately sync a Railway (or any) integration
                             without waiting for the 5-minute poll cycle.
"""
from __future__ import annotations

import asyncio
import traceback
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth.dependencies import TenantContext, require_admin
from ..db.session import get_db
from ..utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter()


# ── /crash ────────────────────────────────────────────────────────────────────

class CrashRequest(BaseModel):
    service_name: str = Field(
        default="payments-api",
        description="Service label that appears in the log line (cosmetic only).",
    )
    error_message: str = Field(
        default="CRITICAL: Database connection pool exhausted — all 20 connections in use",
        description="The error text to log. Should match keywords in your Jira ticket.",
    )
    repeat: int = Field(
        default=8,
        ge=1,
        le=50,
        description="How many times to log the error. Must exceed LOG_FAST_ALERT_THRESHOLD (default 5).",
    )


class CrashResponse(BaseModel):
    logged: int
    error_message: str
    next_step: str


@router.post("/crash", response_model=CrashResponse, status_code=202)
async def trigger_crash(
    body: CrashRequest,
    ctx: Annotated[TenantContext, Depends(require_admin)],
):
    """
    Writes a realistic Python traceback to stdout **repeat** times so Railway
    captures it as real logs.

    After calling this:
    1. Call POST /dev/force-sync to pull the logs immediately, OR
    2. Wait ~5 minutes for the scheduler to pick them up automatically.

    OpsLens will detect the error spike, query Qdrant for related Jira tickets,
    and fire a Slack alert + generate an RRT brief.
    """
    # Build a realistic traceback that matches _CRITICAL_RE and shows up clearly
    fake_tb = (
        f"Traceback (most recent call last):\n"
        f'  File "/app/{body.service_name}/handlers.py", line 87, in handle_request\n'
        f'    result = await db_pool.acquire(timeout=5.0)\n'
        f'  File "/app/core/db.py", line 134, in acquire\n'
        f'    raise PoolExhaustedError(msg)\n'
        f"PoolExhaustedError: {body.error_message}"
    )

    for i in range(body.repeat):
        # Use ERROR level so it matches _CRITICAL_RE in fast_scan / _trigger_log_source_incidents
        logger.error(
            "[%s] %s\n%s",
            body.service_name,
            body.error_message,
            fake_tb,
        )
        # Small stagger so lines have distinct timestamps in Railway
        await asyncio.sleep(0.05)

    logger.error(
        "DEMO: %d error(s) logged for service '%s' — trigger a sync or wait for scheduler",
        body.repeat, body.service_name,
    )

    return CrashResponse(
        logged=body.repeat,
        error_message=body.error_message,
        next_step="Call POST /api/v1/dev/force-sync to pull these logs into OpsLens immediately.",
    )


# ── /force-sync ───────────────────────────────────────────────────────────────

class ForceSyncRequest(BaseModel):
    source_type: str = Field(
        default="railway",
        description="Integration source type to sync: 'railway', 'datadog', 'elasticsearch', etc.",
    )


class ForceSyncResponse(BaseModel):
    synced: bool
    integration_id: str | None
    source_type: str
    records_added: int | None
    message: str


@router.post("/force-sync", response_model=ForceSyncResponse, status_code=202)
async def force_sync(
    body: ForceSyncRequest,
    ctx: Annotated[TenantContext, Depends(require_admin)],
    db=Depends(get_db),
):
    """
    Immediately syncs the named integration for this tenant, bypassing the
    5-minute scheduler cycle.

    After syncing, newly captured error logs are processed through
    _trigger_log_source_incidents → enrich_and_alert → RRT brief pipeline
    in real time.

    Use after POST /dev/crash to close the demo loop in seconds rather than
    waiting for the next poll cycle.
    """
    from ..db.models import Integration
    from ..utils.crypto import decrypt_credentials
    from ..services.direct_sync_service import run_direct_sync

    # Find the active integration for this tenant + source_type
    result = await db.execute(
        sa.select(Integration).where(
            Integration.tenant_id == ctx.tenant_uuid,
            Integration.source_type == body.source_type,
            Integration.status == "active",
        ).limit(1)
    )
    integration: Integration | None = result.scalar_one_or_none()

    if not integration:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No active '{body.source_type}' integration found for your account. "
                f"Connect it first via Integrations page."
            ),
        )

    integration_id = str(integration.id)
    tenant_id = str(ctx.tenant_uuid)

    logger.info(
        "Force sync triggered: source=%s integration=%s tenant=%s",
        body.source_type, integration_id[:8], tenant_id,
    )

    try:
        records_added = await run_direct_sync(integration_id, tenant_id)
        return ForceSyncResponse(
            synced=True,
            integration_id=integration_id,
            source_type=body.source_type,
            records_added=records_added if isinstance(records_added, int) else None,
            message=(
                f"Sync complete. New records ingested and scanned for incidents. "
                f"Check Slack and /rrt-briefs in ~30 seconds."
            ),
        )
    except Exception as exc:
        logger.error("Force sync failed: %s\n%s", exc, traceback.format_exc())
        raise HTTPException(
            status_code=500,
            detail=f"Sync failed: {exc}",
        )
