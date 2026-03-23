"""
OpsLens AI — Retention Policy Router
=======================================
Manage data lifecycle policies and trigger manual cleanup runs.

  GET  /api/v1/retention               — Get current retention policy (admin)
  POST /api/v1/retention               — Create or update retention policy (admin)
  POST /api/v1/retention/run           — Trigger manual cleanup now (admin)
  GET  /api/v1/retention/stats         — Last run stats + projected deletions (admin)
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.auth.middleware import require_roles
from apps.api.db.session import get_db
from apps.api.models.retention import RetentionPolicy

router = APIRouter()


# ── Schemas ───────────────────────────────────────────────────────────────────

class RetentionPolicyIn(BaseModel):
    log_scan_history_days:  int = Field(90,  ge=0, le=3650)
    timeline_event_days:    int = Field(180, ge=0, le=3650)
    rrt_brief_days:         int = Field(365, ge=0, le=3650)
    audit_log_days:         int = Field(730, ge=0, le=3650)
    chat_session_days:      int = Field(90,  ge=0, le=3650)
    insight_days:           int = Field(90,  ge=0, le=3650)
    embedding_days:         int = Field(180, ge=0, le=3650)
    archive_to_s3:          bool = False
    s3_bucket:              str | None = None
    s3_prefix:              str | None = None


def _policy_to_dict(p: RetentionPolicy) -> dict:
    return {
        "id": p.id,
        "tenant_id": p.tenant_id,
        "log_scan_history_days": p.log_scan_history_days,
        "timeline_event_days": p.timeline_event_days,
        "rrt_brief_days": p.rrt_brief_days,
        "audit_log_days": p.audit_log_days,
        "chat_session_days": p.chat_session_days,
        "insight_days": p.insight_days,
        "embedding_days": p.embedding_days,
        "archive_to_s3": p.archive_to_s3,
        "s3_bucket": p.s3_bucket,
        "s3_prefix": p.s3_prefix,
        "last_run_at": p.last_run_at.isoformat() if p.last_run_at else None,
        "last_deleted_rows": p.last_deleted_rows,
        "last_run_notes": p.last_run_notes,
        "is_active": p.is_active,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


async def _get_policy(tenant_id: str, db: AsyncSession) -> RetentionPolicy | None:
    result = await db.execute(
        sa.select(RetentionPolicy).where(RetentionPolicy.tenant_id == tenant_id)
    )
    return result.scalar_one_or_none()


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get(
    "",
    summary="Get retention policy for tenant",
)
async def get_retention_policy(
    tenant_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
    _auth=Depends(require_roles(["admin"])),
):
    policy = await _get_policy(tenant_id, db)
    if not policy:
        # Return defaults (no row saved yet)
        return RetentionPolicyIn().model_dump() | {
            "id": None,
            "tenant_id": tenant_id,
            "last_run_at": None,
            "last_deleted_rows": None,
        }
    return _policy_to_dict(policy)


@router.post(
    "",
    summary="Create or update retention policy (admin only)",
    status_code=status.HTTP_200_OK,
)
async def upsert_retention_policy(
    body: RetentionPolicyIn,
    tenant_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
    _auth=Depends(require_roles(["admin"])),
):
    existing = await _get_policy(tenant_id, db)
    if existing:
        for field, value in body.model_dump().items():
            setattr(existing, field, value)
        existing.updated_at = datetime.now(tz=timezone.utc)
        await db.commit()
        return _policy_to_dict(existing)
    else:
        policy = RetentionPolicy(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            **body.model_dump(),
        )
        db.add(policy)
        await db.commit()
        return _policy_to_dict(policy)


@router.post(
    "/run",
    summary="Trigger manual retention cleanup now (admin only)",
)
async def trigger_cleanup(
    tenant_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
    _auth=Depends(require_roles(["admin"])),
):
    """
    Immediately dispatches the retention.run_cleanup Celery task for this tenant.
    Returns the Celery task ID for status polling.
    """
    try:
        from apps.worker.tasks.retention import run_cleanup
        task = run_cleanup.delay(tenant_id=tenant_id)
        return {
            "status": "dispatched",
            "task_id": task.id,
            "message": f"Retention cleanup dispatched for tenant {tenant_id}",
        }
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Could not dispatch cleanup task: {exc}",
        )


@router.get(
    "/stats",
    summary="Retention stats and projected deletion counts (admin only)",
)
async def get_retention_stats(
    tenant_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
    _auth=Depends(require_roles(["admin"])),
):
    """
    Returns how many rows would be deleted if the retention policy ran right now.
    Useful for auditing before adjusting retention periods.
    """
    from apps.api.models.audit import AuditLog
    from apps.api.models.timeline import TimelineEvent
    from apps.api.models.rrt_brief import RRTBrief

    policy = await _get_policy(tenant_id, db)
    if not policy:
        return {"message": "No retention policy configured for this tenant."}

    def cutoff(days: int) -> datetime | None:
        return datetime.now(tz=timezone.utc) - timedelta(days=days) if days else None

    counts: dict[str, int | None] = {}

    # Count eligible rows per category
    try:
        if policy.timeline_event_days:
            c = await db.execute(sa.select(sa.func.count()).where(
                TimelineEvent.tenant_id == tenant_id,
                TimelineEvent.occurred_at <= cutoff(policy.timeline_event_days),
            ))
            counts["timeline_events"] = c.scalar_one() or 0
        else:
            counts["timeline_events"] = 0
    except Exception:
        counts["timeline_events"] = None

    try:
        if policy.rrt_brief_days:
            c = await db.execute(sa.select(sa.func.count()).where(
                RRTBrief.tenant_id == tenant_id,
                RRTBrief.detected_at <= cutoff(policy.rrt_brief_days),
                RRTBrief.status == "resolved",
            ))
            counts["rrt_briefs"] = c.scalar_one() or 0
        else:
            counts["rrt_briefs"] = 0
    except Exception:
        counts["rrt_briefs"] = None

    try:
        if policy.audit_log_days:
            c = await db.execute(sa.select(sa.func.count()).where(
                AuditLog.tenant_id == tenant_id,
                AuditLog.occurred_at <= cutoff(policy.audit_log_days),
            ))
            counts["audit_logs"] = c.scalar_one() or 0
        else:
            counts["audit_logs"] = 0
    except Exception:
        counts["audit_logs"] = None

    return {
        "tenant_id": tenant_id,
        "policy": _policy_to_dict(policy),
        "projected_deletions": counts,
        "note": "Run POST /api/v1/retention/run to execute cleanup now.",
    }
