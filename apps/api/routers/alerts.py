"""
OpsLens AI — Alerts Router
===========================
Manages alert rule configuration and alert history.

Endpoints:
    GET    /api/v1/alerts/rules          — List rules
    POST   /api/v1/alerts/rules          — Create rule
    GET    /api/v1/alerts/rules/{id}     — Get rule
    PATCH  /api/v1/alerts/rules/{id}     — Update rule
    DELETE /api/v1/alerts/rules/{id}     — Delete rule
    POST   /api/v1/alerts/test/{rule_id} — Test-fire a rule
    GET    /api/v1/alerts/history        — List fired alerts
"""
from __future__ import annotations

import uuid
from typing import Annotated, Literal

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
VALID_CHANNELS   = {"slack", "email"}
VALID_OPERATORS  = {"gt", "gte", "lt", "lte", "eq", "contains"}
VALID_FIELDS     = {"magnitude", "insight_type", "title"}


# ── Schemas ────────────────────────────────────────────────────────────────────
class AlertCondition(BaseModel):
    field:    str = Field(..., description="Field to evaluate: magnitude | insight_type | title")
    operator: str = Field(..., description="gt | gte | lt | lte | eq | contains")
    value:    float | str = Field(..., description="Comparison value")

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


class ChannelConfig(BaseModel):
    slack_webhook_url:  str | None = None
    email_recipients:   list[str] = Field(default_factory=list)


class CreateRuleRequest(BaseModel):
    name:           str = Field(..., min_length=1, max_length=100)
    insight_types:  list[str] = Field(..., min_length=1)
    condition:      AlertCondition
    channels:       list[str] = Field(..., min_length=1)
    channel_config: ChannelConfig = Field(default_factory=ChannelConfig)
    cooldown_hours: int = Field(default=4, ge=1, le=168)
    enabled:        bool = True

    @field_validator("insight_types")
    @classmethod
    def check_insight_types(cls, v: list[str]) -> list[str]:
        bad = set(v) - VALID_INSIGHT_TYPES
        if bad:
            raise ValueError(f"Invalid insight_types: {bad}. Valid: {VALID_INSIGHT_TYPES}")
        return v

    @field_validator("channels")
    @classmethod
    def check_channels(cls, v: list[str]) -> list[str]:
        bad = set(v) - VALID_CHANNELS
        if bad:
            raise ValueError(f"Invalid channels: {bad}. Valid: {VALID_CHANNELS}")
        return v


class UpdateRuleRequest(BaseModel):
    name:           str | None = None
    condition:      AlertCondition | None = None
    channels:       list[str] | None = None
    channel_config: ChannelConfig | None = None
    cooldown_hours: int | None = Field(default=None, ge=1, le=168)
    enabled:        bool | None = None


class AlertRuleOut(BaseModel):
    id:             str
    name:           str
    insight_types:  list[str]
    condition:      dict
    channels:       list[str]
    cooldown_hours: int
    last_fired_at:  str | None
    enabled:        bool
    created_at:     str


class AlertHistoryOut(BaseModel):
    id:              str
    rule_id:         str | None
    insight_id:      str | None
    rule_name:       str | None
    channels_sent:   list[str]
    delivery_status: str
    fired_at:        str


# ── List rules ─────────────────────────────────────────────────────────────────
@router.get("/rules", response_model=list[AlertRuleOut])
async def list_rules(
    ctx: Annotated[TenantContext, Depends(require_viewer)],
    db=Depends(get_db),
):
    result = await db.execute(
        sa.select(AlertRule)
        .where(AlertRule.tenant_id == ctx.tenant_id)
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
        id=str(uuid.uuid4()),
        tenant_id=ctx.tenant_id,
        created_by=ctx.user_id,
        name=body.name,
        insight_types=body.insight_types,
        condition=body.condition.model_dump(),
        channels=body.channels,
        channel_config=body.channel_config.model_dump(),
        cooldown_hours=body.cooldown_hours,
        enabled=body.enabled,
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
    return _rule_to_out(await _get_rule_or_404(db, rule_id, ctx.tenant_id))


# ── Update rule ────────────────────────────────────────────────────────────────
@router.patch("/rules/{rule_id}", response_model=AlertRuleOut)
async def update_rule(
    rule_id: str,
    body: UpdateRuleRequest,
    ctx: Annotated[TenantContext, Depends(require_admin)],
    db=Depends(get_db),
):
    rule = await _get_rule_or_404(db, rule_id, ctx.tenant_id)

    if body.name          is not None: rule.name           = body.name
    if body.condition     is not None: rule.condition      = body.condition.model_dump()
    if body.channels      is not None: rule.channels       = body.channels
    if body.channel_config is not None: rule.channel_config = body.channel_config.model_dump()
    if body.cooldown_hours is not None: rule.cooldown_hours = body.cooldown_hours
    if body.enabled       is not None: rule.enabled        = body.enabled

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
    rule = await _get_rule_or_404(db, rule_id, ctx.tenant_id)
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
    """
    Send a test notification via all channels configured on this rule.
    Uses a synthetic insight payload — does NOT create a real insight.
    """
    from ..services.alert_service import AlertService
    rule = await _get_rule_or_404(db, rule_id, ctx.tenant_id)

    svc = AlertService()
    results = await svc.dispatch_test(rule)
    logger.info("Test-fire alert rule %s by %s → %s", rule_id, ctx.user_id, results)
    return {"message": "Test notification dispatched", "results": results}


# ── Alert history ──────────────────────────────────────────────────────────────
@router.get("/history", response_model=list[AlertHistoryOut])
async def get_alert_history(
    ctx: Annotated[TenantContext, Depends(require_viewer)],
    db=Depends(get_db),
    days: int = Query(default=7, ge=1, le=90),
    limit: int = Query(default=50, ge=1, le=200),
):
    from datetime import timedelta
    from datetime import datetime
    cutoff = datetime.now(tz=__import__("datetime").timezone.utc) - timedelta(days=days)
    result = await db.execute(
        sa.select(AlertHistory, AlertRule.name.label("rule_name"))
        .outerjoin(AlertRule, AlertHistory.rule_id == AlertRule.id)
        .where(
            AlertHistory.tenant_id == ctx.tenant_id,
            AlertHistory.fired_at >= cutoff,
        )
        .order_by(AlertHistory.fired_at.desc())
        .limit(limit)
    )
    rows = result.all()
    return [
        AlertHistoryOut(
            id=str(h.id),
            rule_id=str(h.rule_id) if h.rule_id else None,
            insight_id=str(h.insight_id) if h.insight_id else None,
            rule_name=rule_name,
            channels_sent=list(h.channels_sent or []),
            delivery_status=h.delivery_status,
            fired_at=h.fired_at.isoformat(),
        )
        for h, rule_name in rows
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
        insight_types=list(r.insight_types or []),
        condition=dict(r.condition or {}),
        channels=list(r.channels or []),
        cooldown_hours=r.cooldown_hours,
        last_fired_at=r.last_fired_at.isoformat() if r.last_fired_at else None,
        enabled=r.enabled,
        created_at=r.created_at.isoformat(),
    )
