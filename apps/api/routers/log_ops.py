"""
OpsLens AI — Log Operations Router
=====================================
Manages known issue suppression and team alert routing rules.

Known Issues (suppress recurring alerts):
    GET    /api/v1/log-ops/known-issues              — list suppressions
    POST   /api/v1/log-ops/known-issues              — create suppression
    PATCH  /api/v1/log-ops/known-issues/{id}         — update (extend snooze, etc.)
    DELETE /api/v1/log-ops/known-issues/{id}         — remove suppression (re-arm)

Routing Rules (route alerts to teams):
    GET    /api/v1/log-ops/routing-rules             — list rules
    POST   /api/v1/log-ops/routing-rules             — create rule
    PATCH  /api/v1/log-ops/routing-rules/{id}        — update rule
    DELETE /api/v1/log-ops/routing-rules/{id}        — delete rule
    POST   /api/v1/log-ops/routing-rules/test        — test which rules match a given error
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from ..auth.dependencies import TenantContext, require_admin, require_viewer
from ..db.session import get_db
from ..models.log_ops import AlertRoutingRule, KnownIssue
from ..utils.logging import get_logger

router = APIRouter()
logger = get_logger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# KNOWN ISSUES — schemas
# ═══════════════════════════════════════════════════════════════════════════════

class CreateKnownIssueRequest(BaseModel):
    signature:       str | None = Field(
        None,
        description="Error signature hash from a fast-alert notification. "
                    "Provide this OR match_pattern."
    )
    match_pattern:   str | None = Field(
        None,
        description="Regex or keyword to match against the error line. "
                    "Example: 'KeyError.*tenant_id' or 'ConnectionRefused'"
    )
    description:     str | None = Field(None, description="Why this is suppressed / Jira link")
    suppress_until:  datetime | None = Field(
        None,
        description="Snooze expiry (ISO datetime). Leave null for permanent suppression."
    )
    jira_ticket_key: str | None = Field(
        None,
        description="e.g. 'OPS-142'. Auto-suppresses while ticket is not Done."
    )


class UpdateKnownIssueRequest(BaseModel):
    description:     str | None = None
    suppress_until:  datetime | None = None
    jira_ticket_key: str | None = None
    is_active:       bool | None = None


class KnownIssueOut(BaseModel):
    id:              str
    signature:       str | None
    match_pattern:   str | None
    description:     str | None
    suppressed_by:   str | None
    suppress_until:  str | None
    jira_ticket_key: str | None
    hit_count:       int
    last_hit_at:     str | None
    is_active:       bool
    created_at:      str


# ═══════════════════════════════════════════════════════════════════════════════
# KNOWN ISSUES — endpoints
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/known-issues", response_model=list[KnownIssueOut])
async def list_known_issues(
    ctx: Annotated[TenantContext, Depends(require_viewer)],
    db=Depends(get_db),
    active_only: bool = Query(default=True),
):
    """List all known issue suppressions for this tenant."""
    q = sa.select(KnownIssue).where(KnownIssue.tenant_id == ctx.tenant_id)
    if active_only:
        q = q.where(KnownIssue.is_active == True)
    result = await db.execute(q.order_by(KnownIssue.created_at.desc()))
    return [_ki_to_out(r) for r in result.scalars().all()]


@router.post("/known-issues", response_model=KnownIssueOut, status_code=status.HTTP_201_CREATED)
async def create_known_issue(
    body: CreateKnownIssueRequest,
    ctx: Annotated[TenantContext, Depends(require_admin)],
    db=Depends(get_db),
):
    """
    Suppress a recurring alert. Provide either a signature hash (from the
    Slack notification) or a match_pattern regex/keyword.
    """
    if not body.signature and not body.match_pattern:
        raise HTTPException(
            status_code=400,
            detail="Provide at least one of: signature, match_pattern"
        )
    # Validate regex if provided
    if body.match_pattern:
        try:
            re.compile(body.match_pattern)
        except re.error as e:
            raise HTTPException(status_code=400, detail=f"Invalid regex pattern: {e}")

    ki = KnownIssue(
        id=str(uuid.uuid4()),
        tenant_id=ctx.tenant_id,
        signature=body.signature,
        match_pattern=body.match_pattern,
        description=body.description,
        suppressed_by=ctx.user_id,
        suppress_until=body.suppress_until,
        jira_ticket_key=body.jira_ticket_key,
        is_active=True,
    )
    db.add(ki)
    await db.commit()
    await db.refresh(ki)
    logger.info("Known issue created by %s: sig=%s pattern=%s", ctx.user_id, body.signature, body.match_pattern)
    return _ki_to_out(ki)


@router.patch("/known-issues/{issue_id}", response_model=KnownIssueOut)
async def update_known_issue(
    issue_id: str,
    body: UpdateKnownIssueRequest,
    ctx: Annotated[TenantContext, Depends(require_admin)],
    db=Depends(get_db),
):
    """Update a suppression — extend snooze, link Jira ticket, or re-arm (is_active=true)."""
    ki = await _get_ki_or_404(db, issue_id, ctx.tenant_id)
    if body.description     is not None: ki.description     = body.description
    if body.suppress_until  is not None: ki.suppress_until  = body.suppress_until
    if body.jira_ticket_key is not None: ki.jira_ticket_key = body.jira_ticket_key
    if body.is_active       is not None: ki.is_active       = body.is_active
    await db.commit()
    await db.refresh(ki)
    return _ki_to_out(ki)


@router.delete("/known-issues/{issue_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_known_issue(
    issue_id: str,
    ctx: Annotated[TenantContext, Depends(require_admin)],
    db=Depends(get_db),
):
    """Remove a suppression — the exception will start alerting again."""
    ki = await _get_ki_or_404(db, issue_id, ctx.tenant_id)
    await db.delete(ki)
    await db.commit()
    logger.info("Known issue %s deleted (re-armed) by %s", issue_id, ctx.user_id)


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTING RULES — schemas
# ═══════════════════════════════════════════════════════════════════════════════

class CreateRoutingRuleRequest(BaseModel):
    team_name:         str = Field(..., min_length=1, max_length=100)
    description:       str | None = None
    service_patterns:  list[str] = Field(
        default=[],
        description="Keywords/regex matched against the full error line. "
                    "e.g. ['payment', 'stripe', 'checkout']"
    )
    error_patterns:    list[str] = Field(
        default=[],
        description="Keywords/regex matched against the exception class/message. "
                    "e.g. ['PaymentError', 'StripeTimeout', 'CardDeclined']"
    )
    source_containers: list[str] = Field(
        default=[],
        description="Docker container names to scope this rule. "
                    "e.g. ['payment-service', 'billing-worker']"
    )
    match_all:         bool = Field(
        default=False,
        description="If true, ALL non-empty pattern lists must match. "
                    "If false (default), any single match triggers the rule."
    )
    slack_webhook:     str | None = None
    email_recipients:  list[str] = Field(default=[])
    priority:          int = Field(default=100, ge=1, le=999,
                                   description="Lower = evaluated first. Use 999 for catch-all.")
    stop_on_match:     bool = Field(
        default=False,
        description="Stop evaluating further rules after this one matches."
    )
    is_active:         bool = True


class UpdateRoutingRuleRequest(BaseModel):
    team_name:         str | None = None
    description:       str | None = None
    service_patterns:  list[str] | None = None
    error_patterns:    list[str] | None = None
    source_containers: list[str] | None = None
    match_all:         bool | None = None
    slack_webhook:     str | None = None
    email_recipients:  list[str] | None = None
    priority:          int | None = Field(default=None, ge=1, le=999)
    stop_on_match:     bool | None = None
    is_active:         bool | None = None


class RoutingRuleOut(BaseModel):
    id:                str
    team_name:         str
    description:       str | None
    service_patterns:  list[str]
    error_patterns:    list[str]
    source_containers: list[str]
    match_all:         bool
    slack_webhook:     str | None
    email_recipients:  list[str]
    priority:          int
    stop_on_match:     bool
    is_active:         bool
    created_at:        str


class TestRoutingRequest(BaseModel):
    error_line:  str = Field(..., description="A sample error log line to test against rules")
    container:   str | None = Field(None, description="Container name (optional)")


class TestRoutingResult(BaseModel):
    matched_rules: list[dict]
    unmatched_rules: list[str]
    would_notify: list[str]   # deduplicated list of webhooks/emails that would fire


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTING RULES — endpoints
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/routing-rules", response_model=list[RoutingRuleOut])
async def list_routing_rules(
    ctx: Annotated[TenantContext, Depends(require_viewer)],
    db=Depends(get_db),
):
    result = await db.execute(
        sa.select(AlertRoutingRule)
        .where(AlertRoutingRule.tenant_id == ctx.tenant_id)
        .order_by(AlertRoutingRule.priority, AlertRoutingRule.created_at)
    )
    return [_rr_to_out(r) for r in result.scalars().all()]


@router.post("/routing-rules", response_model=RoutingRuleOut, status_code=status.HTTP_201_CREATED)
async def create_routing_rule(
    body: CreateRoutingRuleRequest,
    ctx: Annotated[TenantContext, Depends(require_admin)],
    db=Depends(get_db),
):
    """
    Create a team routing rule.

    Example — route all payment errors to the payments team:
        {
          "team_name": "Payments Team",
          "service_patterns": ["payment", "stripe", "checkout", "billing"],
          "error_patterns": ["PaymentError", "CardDeclined", "StripeTimeout"],
          "slack_webhook": "https://hooks.slack.com/services/PAYMENTS_WEBHOOK",
          "email_recipients": ["payments-oncall@company.com"],
          "priority": 10,
          "stop_on_match": false
        }
    """
    if not body.service_patterns and not body.error_patterns and not body.source_containers:
        raise HTTPException(
            status_code=400,
            detail="Provide at least one of: service_patterns, error_patterns, source_containers"
        )
    # Validate all patterns as valid regex
    for p in (body.service_patterns + body.error_patterns):
        try:
            re.compile(p, re.IGNORECASE)
        except re.error as e:
            raise HTTPException(status_code=400, detail=f"Invalid pattern '{p}': {e}")

    rr = AlertRoutingRule(
        id=str(uuid.uuid4()),
        tenant_id=ctx.tenant_id,
        team_name=body.team_name,
        description=body.description,
        service_patterns=body.service_patterns,
        error_patterns=body.error_patterns,
        source_containers=body.source_containers,
        match_all=body.match_all,
        slack_webhook=body.slack_webhook,
        email_recipients=body.email_recipients,
        priority=body.priority,
        stop_on_match=body.stop_on_match,
        is_active=body.is_active,
    )
    db.add(rr)
    await db.commit()
    await db.refresh(rr)
    logger.info("Routing rule '%s' created by %s (priority=%d)", body.team_name, ctx.user_id, body.priority)
    return _rr_to_out(rr)


@router.patch("/routing-rules/{rule_id}", response_model=RoutingRuleOut)
async def update_routing_rule(
    rule_id: str,
    body: UpdateRoutingRuleRequest,
    ctx: Annotated[TenantContext, Depends(require_admin)],
    db=Depends(get_db),
):
    rr = await _get_rr_or_404(db, rule_id, ctx.tenant_id)
    if body.team_name         is not None: rr.team_name         = body.team_name
    if body.description       is not None: rr.description       = body.description
    if body.service_patterns  is not None: rr.service_patterns  = body.service_patterns
    if body.error_patterns    is not None: rr.error_patterns    = body.error_patterns
    if body.source_containers is not None: rr.source_containers = body.source_containers
    if body.match_all         is not None: rr.match_all         = body.match_all
    if body.slack_webhook     is not None: rr.slack_webhook     = body.slack_webhook
    if body.email_recipients  is not None: rr.email_recipients  = body.email_recipients
    if body.priority          is not None: rr.priority          = body.priority
    if body.stop_on_match     is not None: rr.stop_on_match     = body.stop_on_match
    if body.is_active         is not None: rr.is_active         = body.is_active
    await db.commit()
    await db.refresh(rr)
    return _rr_to_out(rr)


@router.delete("/routing-rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_routing_rule(
    rule_id: str,
    ctx: Annotated[TenantContext, Depends(require_admin)],
    db=Depends(get_db),
):
    rr = await _get_rr_or_404(db, rule_id, ctx.tenant_id)
    await db.delete(rr)
    await db.commit()


@router.post("/routing-rules/test", response_model=TestRoutingResult)
async def test_routing_rules(
    body: TestRoutingRequest,
    ctx: Annotated[TenantContext, Depends(require_viewer)],
    db=Depends(get_db),
):
    """
    Dry-run: paste an error line and see which teams would be notified.
    Useful for validating patterns before enabling rules in production.
    """
    result = await db.execute(
        sa.select(AlertRoutingRule)
        .where(
            AlertRoutingRule.tenant_id == ctx.tenant_id,
            AlertRoutingRule.is_active == True,
        )
        .order_by(AlertRoutingRule.priority)
    )
    rules = result.scalars().all()

    matched, unmatched = [], []
    webhooks: set[str] = set()
    emails: set[str] = set()

    for rr in rules:
        if _rule_matches(rr, body.error_line, body.container):
            matched.append({
                "id": str(rr.id),
                "team_name": rr.team_name,
                "priority": rr.priority,
                "slack_webhook": rr.slack_webhook,
                "email_recipients": list(rr.email_recipients or []),
                "stop_on_match": rr.stop_on_match,
            })
            if rr.slack_webhook:
                webhooks.add(rr.slack_webhook)
            for e in (rr.email_recipients or []):
                emails.add(e)
            if rr.stop_on_match:
                break
        else:
            unmatched.append(rr.team_name)

    return TestRoutingResult(
        matched_rules=matched,
        unmatched_rules=unmatched,
        would_notify=list(webhooks) + list(emails),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Shared matching logic (also used by log_fast_alert.py)
# ═══════════════════════════════════════════════════════════════════════════════

def _rule_matches(rule: AlertRoutingRule, error_line: str, container: str | None) -> bool:
    """
    Returns True if the routing rule matches the given error line + container.
    match_all=False: any single pattern hit is sufficient.
    match_all=True:  all non-empty pattern groups must have at least one hit.
    """
    def _any_match(patterns: list[str], text: str) -> bool:
        if not patterns:
            return False
        for p in patterns:
            try:
                if re.search(p, text, re.IGNORECASE):
                    return True
            except re.error:
                if p.lower() in text.lower():
                    return True
        return False

    service_hit   = _any_match(list(rule.service_patterns or []), error_line)
    error_hit     = _any_match(list(rule.error_patterns or []), error_line)
    container_hit = (
        container in (rule.source_containers or [])
        if rule.source_containers
        else False
    )

    if rule.match_all:
        # Each non-empty list must have a hit
        checks = []
        if rule.service_patterns:   checks.append(service_hit)
        if rule.error_patterns:     checks.append(error_hit)
        if rule.source_containers:  checks.append(container_hit)
        return bool(checks) and all(checks)
    else:
        return service_hit or error_hit or container_hit


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

async def _get_ki_or_404(db, issue_id: str, tenant_id: str) -> KnownIssue:
    r = await db.execute(
        sa.select(KnownIssue).where(
            KnownIssue.id == issue_id,
            KnownIssue.tenant_id == tenant_id,
        )
    )
    obj = r.scalar_one_or_none()
    if not obj:
        raise HTTPException(status_code=404, detail="Known issue not found")
    return obj


async def _get_rr_or_404(db, rule_id: str, tenant_id: str) -> AlertRoutingRule:
    r = await db.execute(
        sa.select(AlertRoutingRule).where(
            AlertRoutingRule.id == rule_id,
            AlertRoutingRule.tenant_id == tenant_id,
        )
    )
    obj = r.scalar_one_or_none()
    if not obj:
        raise HTTPException(status_code=404, detail="Routing rule not found")
    return obj


def _ki_to_out(ki: KnownIssue) -> KnownIssueOut:
    return KnownIssueOut(
        id=str(ki.id),
        signature=ki.signature,
        match_pattern=ki.match_pattern,
        description=ki.description,
        suppressed_by=ki.suppressed_by,
        suppress_until=ki.suppress_until.isoformat() if ki.suppress_until else None,
        jira_ticket_key=ki.jira_ticket_key,
        hit_count=ki.hit_count,
        last_hit_at=ki.last_hit_at.isoformat() if ki.last_hit_at else None,
        is_active=ki.is_active,
        created_at=ki.created_at.isoformat(),
    )


def _rr_to_out(rr: AlertRoutingRule) -> RoutingRuleOut:
    return RoutingRuleOut(
        id=str(rr.id),
        team_name=rr.team_name,
        description=rr.description,
        service_patterns=list(rr.service_patterns or []),
        error_patterns=list(rr.error_patterns or []),
        source_containers=list(rr.source_containers or []),
        match_all=rr.match_all,
        slack_webhook=rr.slack_webhook,
        email_recipients=list(rr.email_recipients or []),
        priority=rr.priority,
        stop_on_match=rr.stop_on_match,
        is_active=rr.is_active,
        created_at=rr.created_at.isoformat(),
    )
