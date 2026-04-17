"""
OpsLens AI — Insights Router
==============================
CRUD and management for automatically-generated operational insights.

Endpoints:
    GET    /api/v1/insights           — List insights (filterable)
    GET    /api/v1/insights/{id}      — Get single insight
    POST   /api/v1/insights/generate  — Trigger on-demand generation (member+)
    PATCH  /api/v1/insights/{id}/status — Resolve or snooze
    GET    /api/v1/insights/summary   — Aggregated counts by type
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from ..auth.dependencies import TenantContext, require_admin, require_member, require_viewer
from ..db.session import get_db
from ..models.insight import Insight
from ..utils.logging import get_logger

router = APIRouter()
logger = get_logger(__name__)

VALID_INSIGHT_TYPES = {
    "complaint_spike",
    "feature_trend",
    "release_correlation",
    "eng_bottleneck",
    "churn_risk",
}
VALID_STATUSES = {"active", "resolved", "snoozed"}


# ── Schemas ────────────────────────────────────────────────────────────────────
class InsightOut(BaseModel):
    id: str
    insight_type: str
    title: str
    summary: str
    magnitude: str | None       # "low" | "medium" | "high"
    confidence: float | None
    status: str
    source_types: list[str]
    generated_at: str
    resolved_at: str | None
    snoozed_until: str | None


class StatusUpdateRequest(BaseModel):
    status: Literal["active", "resolved", "snoozed"]
    snooze_hours: int | None = Field(
        default=None,
        ge=1,
        le=168,
        description="Required when status='snoozed'. Number of hours to snooze.",
    )


# ── List insights ──────────────────────────────────────────────────────────────
@router.get("", response_model=list[InsightOut])
async def list_insights(
    ctx: Annotated[TenantContext, Depends(require_viewer)],
    db=Depends(get_db),
    insight_type: str | None = Query(default=None, description="Filter by insight type"),
    status_filter: str | None = Query(default=None, alias="status"),
    source_type: str | None = Query(default=None, description="Filter by source type (e.g. slack)"),
    days: int = Query(default=7, ge=1, le=90, description="Look-back window in days"),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    """
    List operational insights for the authenticated tenant.

    Supports filtering by insight type, status, source, and date window.
    """
    cutoff = datetime.now(tz=timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    from datetime import timedelta
    cutoff -= timedelta(days=days)

    conditions = [
        Insight.tenant_id == ctx.tenant_uuid,
        Insight.generated_at >= cutoff,
    ]

    if insight_type:
        if insight_type not in VALID_INSIGHT_TYPES:
            raise HTTPException(400, f"Invalid insight_type. Valid: {sorted(VALID_INSIGHT_TYPES)}")
        conditions.append(Insight.insight_type == insight_type)

    if status_filter:
        if status_filter not in VALID_STATUSES:
            raise HTTPException(400, f"Invalid status. Valid: {sorted(VALID_STATUSES)}")
        conditions.append(Insight.status == status_filter)
    else:
        # Default: only show active insights
        conditions.append(Insight.status == "active")

    if source_type:
        conditions.append(Insight.source_types.any(source_type))

    result = await db.execute(
        sa.select(Insight)
        .where(*conditions)
        .order_by(Insight.generated_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return [_insight_to_out(i) for i in result.scalars().all()]


# ── Get single insight ─────────────────────────────────────────────────────────
@router.get("/summary")
async def get_summary(
    ctx: Annotated[TenantContext, Depends(require_viewer)],
    db=Depends(get_db),
    days: int = Query(default=7, ge=1, le=90),
):
    """
    Return aggregated counts: total, active, resolved, snoozed, by_type, by_magnitude.
    Used by the insights page to show quick-glance stats.
    """
    from datetime import timedelta
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)

    status_result = await db.execute(
        sa.select(Insight.status, sa.func.count(Insight.id).label("count"))
        .where(Insight.tenant_id == ctx.tenant_uuid, Insight.generated_at >= cutoff)
        .group_by(Insight.status)
    )
    by_status: dict[str, int] = {row.status: row.count for row in status_result.fetchall()}

    type_result = await db.execute(
        sa.select(Insight.insight_type, sa.func.count(Insight.id).label("count"))
        .where(Insight.tenant_id == ctx.tenant_uuid, Insight.status == "active",
               Insight.generated_at >= cutoff)
        .group_by(Insight.insight_type)
    )
    by_type: dict[str, int] = {row.insight_type: row.count for row in type_result.fetchall()}

    mag_result = await db.execute(
        sa.select(Insight.magnitude, sa.func.count(Insight.id).label("count"))
        .where(Insight.tenant_id == ctx.tenant_uuid, Insight.status == "active",
               Insight.generated_at >= cutoff)
        .group_by(Insight.magnitude)
    )
    by_magnitude: dict[str, int] = {row.magnitude: row.count for row in mag_result.fetchall()}

    return {
        "total":      sum(by_status.values()),
        "active":     by_status.get("active", 0),
        "resolved":   by_status.get("resolved", 0),
        "snoozed":    by_status.get("snoozed", 0),
        "by_type":    by_type,
        "by_magnitude": by_magnitude,
    }


@router.get("/{insight_id}", response_model=InsightOut)
async def get_insight(
    insight_id: str,
    ctx: Annotated[TenantContext, Depends(require_viewer)],
    db=Depends(get_db),
):
    insight = await _get_insight_or_404(db, insight_id, ctx.tenant_uuid)
    return _insight_to_out(insight)


# ── On-demand generation ───────────────────────────────────────────────────────
@router.post("/generate", status_code=status.HTTP_202_ACCEPTED)
async def trigger_generation(
    ctx: Annotated[TenantContext, Depends(require_member)],
    insight_type: str | None = Query(default=None, description="Run specific detector only"),
):
    """
    Dispatch on-demand insight generation via Celery.
    Available to all members (viewer+ can read, member+ can trigger).
    """
    from celery import current_app as celery_app

    if insight_type and insight_type not in VALID_INSIGHT_TYPES:
        raise HTTPException(400, f"Invalid insight_type: {insight_type}")

    tenant_id = str(ctx.tenant_uuid)

    if insight_type:
        # Map insight_type string to the specific Celery task name
        task_map = {
            "complaint_spike":    "insights.complaint_spike",
            "feature_trend":      "insights.feature_trend",
            "eng_bottleneck":     "insights.eng_bottleneck",
        }
        task_name = task_map.get(insight_type, "insights.run_all_for_tenant")
        celery_app.send_task(task_name, args=[tenant_id])
    else:
        celery_app.send_task("insights.run_all_for_tenant", args=[tenant_id])

    logger.info("On-demand insight generation triggered by %s for tenant %s",
                ctx.user_id, tenant_id)
    return {
        "message": "Insight generation dispatched",
        "tenant_id": tenant_id,
        "insight_type": insight_type or "all",
    }


# ── Status update (resolve / snooze) ──────────────────────────────────────────
@router.patch("/{insight_id}/status", response_model=InsightOut)
async def update_insight_status(
    insight_id: str,
    body: StatusUpdateRequest,
    ctx: Annotated[TenantContext, Depends(require_member)],
    db=Depends(get_db),
):
    """
    Transition an insight between active / resolved / snoozed states.

    Snoozed insights are suppressed until `snooze_hours` have elapsed.
    They automatically return to 'active' after the snooze period.
    """
    insight = await _get_insight_or_404(db, insight_id, ctx.tenant_uuid)
    now = datetime.now(tz=timezone.utc)

    insight.status = body.status

    if body.status == "resolved":
        insight.resolved_at = now
        insight.snoozed_until = None

    elif body.status == "snoozed":
        if not body.snooze_hours:
            raise HTTPException(400, "snooze_hours is required when status='snoozed'")
        from datetime import timedelta
        insight.snoozed_until = now + timedelta(hours=body.snooze_hours)
        insight.resolved_at = None

    else:  # active
        insight.resolved_at = None
        insight.snoozed_until = None

    await db.commit()
    await db.refresh(insight)
    logger.info("Insight %s → %s by user %s", insight_id, body.status, ctx.user_id)
    return _insight_to_out(insight)


# ── Helpers ────────────────────────────────────────────────────────────────────
async def _get_insight_or_404(db, insight_id: str, tenant_id: str) -> Insight:
    result = await db.execute(
        sa.select(Insight).where(
            Insight.id == insight_id,
            Insight.tenant_id == tenant_id,
        )
    )
    insight = result.scalar_one_or_none()
    if not insight:
        raise HTTPException(status_code=404, detail="Insight not found")
    return insight


def _insight_to_out(i: Insight) -> InsightOut:
    return InsightOut(
        id=str(i.id),
        insight_type=i.insight_type,
        title=i.title,
        summary=i.summary,
        magnitude=i.magnitude,          # keep as string: "low" | "medium" | "high"
        confidence=float(i.confidence) if i.confidence is not None else None,
        status=i.status,
        source_types=list(i.source_types or []),
        generated_at=i.generated_at.isoformat(),
        resolved_at=i.resolved_at.isoformat() if i.resolved_at else None,
        snoozed_until=i.snoozed_until.isoformat() if i.snoozed_until else None,
    )
