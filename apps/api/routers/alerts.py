"""
OpsLens AI — Alerts Router
===========================
Manages alert rule configuration, alert history, and log scan reports.

Endpoints:
    GET    /api/v1/alerts/rules             — List rules
    POST   /api/v1/alerts/rules             — Create rule
    GET    /api/v1/alerts/rules/{id}        — Get rule
    PATCH  /api/v1/alerts/rules/{id}        — Update rule
    DELETE /api/v1/alerts/rules/{id}        — Delete rule
    POST   /api/v1/alerts/test/{rule_id}    — Test-fire a rule
    GET    /api/v1/alerts/history           — List fired alerts

    POST   /api/v1/alerts/logs/scan         — Trigger a log scan immediately
    GET    /api/v1/alerts/logs/history      — List past log scan reports
"""
from __future__ import annotations

import uuid
from typing import Annotated, Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator

from ..auth.dependencies import TenantContext, require_admin, require_viewer
from ..db.session import get_db
from ..models.alert import AlertHistory, AlertRule
from ..utils.logging import get_logger

router = APIRouter()
logger = get_logger(__name__)

VALID_INSIGHT_TYPES = {
    "complaint_spike", "feature_trend", "release_correlation",
    "eng_bottleneck", "churn_risk",
}
VALID_CHANNELS  = {"slack", "email"}
VALID_OPERATORS = {"gt", "gte", "lt", "lte", "eq", "contains"}
VALID_FIELDS    = {"magnitude", "insight_type", "title"}


# ── Schemas ────────────────────────────────────────────────────────────────────
class AlertConditionSchema(BaseModel):
    field:    str = Field(..., description="magnitude | insight_type | title")
    operator: str = Field(..., description="gt | gte | lt | lte | eq | contains")
    value:    float | str

    @field_validator("field")
    @classmethod
    def check_field(cls, v: str) -> str:
        if v not in VALID_FIELDS:
            raise ValueError(f"field must be one of {VALID_FIELDS}")
        return v

    @field_validator("operator")
    @classmethod
    def check_operator(cls, v: str) -> str:
        if v not in VALID_OPERATORS:
            raise ValueError(f"operator must be one of {VALID_OPERATORS}")
        return v


class AlertChannelSchema(BaseModel):
    type:        str = Field(..., description="slack | email")
    webhook_url: str | None = None
    email:       str | None = None


class CreateRuleRequest(BaseModel):
    name:             str = Field(..., min_length=1, max_length=100)
    description:      str | None = None
    conditions:       list[AlertConditionSchema] = Field(..., min_length=1)
    channels:         list[AlertChannelSchema] = Field(..., min_length=1)
    is_active:        bool = True
    cooldown_minutes: int = Field(default=240, ge=1, le=10080)

    @field_validator("channels")
    @classmethod
    def check_channel_types(cls, v: list[AlertChannelSchema]) -> list[AlertChannelSchema]:
        bad = {c.type for c in v} - VALID_CHANNELS
        if bad:
            raise ValueError(f"Invalid channel types: {bad}. Valid: {VALID_CHANNELS}")
        return v


class UpdateRuleRequest(BaseModel):
    name:             str | None = None
    description:      str | None = None
    conditions:       list[AlertConditionSchema] | None = None
    channels:         list[AlertChannelSchema] | None = None
    is_active:        bool | None = None
    cooldown_minutes: int | None = Field(default=None, ge=1, le=10080)


# Output schemas — aligned with the frontend AlertRule / AlertHistoryEntry types
class AlertRuleOut(BaseModel):
    id:               str
    name:             str
    description:      str | None
    conditions:       list[dict]
    channels:         list[dict]
    is_active:        bool
    cooldown_minutes: int
    last_triggered_at: str | None
    created_at:       str
    updated_at:       str


class AlertHistoryOut(BaseModel):
    id:                str
    rule_id:           str | None
    rule_name:         str | None
    trigger_data:      dict
    channels_notified: list[str]
    triggered_at:      str


# ── List rules ─────────────────────────────────────────────────────────────────
@router.get("/rules", response_model=list[AlertRuleOut])
async def list_rules(
    ctx: Annotated[TenantContext, Depends(require_viewer)],
    db=Depends(get_db),
):
    result = await db.execute(
        sa.select(AlertRule)
        .where(AlertRule.tenant_id == ctx.tenant_uuid)
        .order_by(AlertRule.created_at)
    )
    return [_rule_to_out(r) for r in result.scalars().all()]


# ── Create rule ────────────────────────────────────────────────────────────────
@router.post("/rules", response_model=AlertRuleOut, status_code=status.HTTP_201_CREATED)
async def create_rule(
    body: CreateRuleRequest,
    ctx: Annotated[TenantContext, Depends(require_admin)],
    db=Depends(get_db),
):
    rule = AlertRule(
        tenant_id=ctx.tenant_uuid,
        name=body.name,
        description=body.description,
        conditions=[c.model_dump() for c in body.conditions],
        channels=[c.model_dump() for c in body.channels],
        is_active=body.is_active,
        cooldown_minutes=body.cooldown_minutes,
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    logger.info("Alert rule '%s' created by %s", body.name, ctx.user_id)
    return _rule_to_out(rule)


# ── Get rule ───────────────────────────────────────────────────────────────────
@router.get("/rules/{rule_id}", response_model=AlertRuleOut)
async def get_rule(
    rule_id: str,
    ctx: Annotated[TenantContext, Depends(require_viewer)],
    db=Depends(get_db),
):
    return _rule_to_out(await _get_rule_or_404(db, rule_id, ctx.tenant_uuid))


# ── Update rule ────────────────────────────────────────────────────────────────
@router.patch("/rules/{rule_id}", response_model=AlertRuleOut)
async def update_rule(
    rule_id: str,
    body: UpdateRuleRequest,
    ctx: Annotated[TenantContext, Depends(require_admin)],
    db=Depends(get_db),
):
    rule = await _get_rule_or_404(db, rule_id, ctx.tenant_uuid)

    if body.name             is not None: rule.name             = body.name
    if body.description      is not None: rule.description      = body.description
    if body.conditions       is not None: rule.conditions       = [c.model_dump() for c in body.conditions]
    if body.channels         is not None: rule.channels         = [c.model_dump() for c in body.channels]
    if body.is_active        is not None: rule.is_active        = body.is_active
    if body.cooldown_minutes is not None: rule.cooldown_minutes = body.cooldown_minutes

    await db.commit()
    await db.refresh(rule)
    return _rule_to_out(rule)


# ── Delete rule ────────────────────────────────────────────────────────────────
@router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(
    rule_id: str,
    ctx: Annotated[TenantContext, Depends(require_admin)],
    db=Depends(get_db),
):
    rule = await _get_rule_or_404(db, rule_id, ctx.tenant_uuid)
    await db.delete(rule)
    await db.commit()
    logger.info("Alert rule %s deleted by %s", rule_id, ctx.user_id)


# ── Test-fire a rule ───────────────────────────────────────────────────────────
@router.post("/test/{rule_id}", status_code=status.HTTP_202_ACCEPTED)
async def test_rule(
    rule_id: str,
    ctx: Annotated[TenantContext, Depends(require_admin)],
    db=Depends(get_db),
):
    """Send a test notification via all channels on this rule."""
    rule = await _get_rule_or_404(db, rule_id, ctx.tenant_uuid)
    logger.info("Test-fire alert rule %s by %s", rule_id, ctx.user_id)
    # AlertService is optional — return a safe no-op if not available
    try:
        from ..services.alert_service import AlertService
        svc = AlertService()
        results = await svc.dispatch_test(rule)
    except (ImportError, Exception) as exc:
        results = {"error": str(exc)}
    return {"message": "Test notification dispatched", "results": results}


# ── Alert history ──────────────────────────────────────────────────────────────
@router.get("/history", response_model=list[AlertHistoryOut])
async def get_alert_history(
    ctx: Annotated[TenantContext, Depends(require_viewer)],
    db=Depends(get_db),
    days: int = Query(default=7, ge=1, le=90),
    limit: int = Query(default=50, ge=1, le=200),
):
    from datetime import datetime, timedelta, timezone
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)

    result = await db.execute(
        sa.select(AlertHistory, AlertRule.name.label("rule_name"))
        .outerjoin(AlertRule, AlertHistory.rule_id == AlertRule.id)
        .where(
            AlertHistory.tenant_id == ctx.tenant_uuid,
            AlertHistory.triggered_at >= cutoff,
        )
        .order_by(AlertHistory.triggered_at.desc())
        .limit(limit)
    )
    rows = result.all()
    return [
        AlertHistoryOut(
            id=str(h.id),
            rule_id=str(h.rule_id) if h.rule_id else None,
            rule_name=rule_name,
            trigger_data=dict(h.trigger_data or {}),
            channels_notified=list(h.channels_notified or []),
            triggered_at=h.triggered_at.isoformat(),
        )
        for h, rule_name in rows
    ]


# ── Log scan — trigger on demand ──────────────────────────────────────────────
@router.post("/logs/scan", status_code=status.HTTP_202_ACCEPTED)
async def trigger_log_scan(
    ctx: Annotated[TenantContext, Depends(require_admin)],
):
    """
    Immediately enqueue a log scan task.
    The scan runs asynchronously; check /logs/history for results.
    """
    try:
        from apps.worker.tasks.log_scanner import scan_logs_and_report
        task = scan_logs_and_report.delay(tenant_id=ctx.tenant_uuid)
        logger.info("Manual log scan triggered by %s (task_id=%s)", ctx.user_id, task.id)
        return {"message": "Log scan enqueued", "task_id": task.id}
    except Exception as exc:
        logger.error("Failed to enqueue log scan: %s", exc)
        raise HTTPException(status_code=500, detail=f"Failed to enqueue log scan: {exc}")


# ── Log scan — history ─────────────────────────────────────────────────────────
class LogScanReportOut(BaseModel):
    id:             str
    scanned_at:     str
    window_minutes: int
    issue_count:    int
    summary:        str | None
    channels_sent:  list[str]


@router.get("/logs/history", response_model=list[LogScanReportOut])
async def get_log_scan_history(
    ctx: Annotated[TenantContext, Depends(require_viewer)],
    db=Depends(get_db),
    days: int = Query(default=7, ge=1, le=90),
    limit: int = Query(default=20, ge=1, le=100),
):
    """Return recent log scan reports for this tenant."""
    from datetime import datetime, timedelta, timezone

    try:
        from ..models.log_scan import LogScanHistory
    except ImportError:
        return []

    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
    result = await db.execute(
        sa.select(LogScanHistory)
        .where(
            LogScanHistory.tenant_id.in_([ctx.tenant_uuid, "system"]),
            LogScanHistory.scanned_at >= cutoff,
        )
        .order_by(LogScanHistory.scanned_at.desc())
        .limit(limit)
    )
    rows = result.scalars().all()
    return [
        LogScanReportOut(
            id=str(r.id),
            scanned_at=r.scanned_at.isoformat(),
            window_minutes=r.window_minutes,
            issue_count=r.issue_count,
            summary=r.summary,
            channels_sent=list(r.channels_sent or []),
        )
        for r in rows
    ]


# ── Helpers ────────────────────────────────────────────────────────────────────
async def _get_rule_or_404(db, rule_id: str, tenant_id: str) -> AlertRule:
    result = await db.execute(
        sa.select(AlertRule).where(
            AlertRule.id == rule_id,
            AlertRule.tenant_id == tenant_id,
        )
    )
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Alert rule not found")
    return rule


def _rule_to_out(r: AlertRule) -> AlertRuleOut:
    return AlertRuleOut(
        id=str(r.id),
        name=r.name,
        description=r.description,
        conditions=list(r.conditions or []),
        channels=list(r.channels or []),
        is_active=r.is_active,
        cooldown_minutes=r.cooldown_minutes,
        last_triggered_at=r.last_triggered_at.isoformat() if r.last_triggered_at else None,
        created_at=r.created_at.isoformat(),
        updated_at=r.updated_at.isoformat(),
    )
