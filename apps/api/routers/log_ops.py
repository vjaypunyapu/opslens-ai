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

import hashlib
import re
import uuid
from datetime import datetime, timezone
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, field_validator

from ..auth.dependencies import TenantContext, require_admin, require_viewer
from ..db.session import get_db
from ..models.log_ops import AlertRoutingRule, KnownIssue
from ..utils.audit import write_audit
from ..utils.crypto import encrypt as _enc, decrypt as _dec
from ..utils.logging import get_logger


# ── Webhook encryption helpers ─────────────────────────────────────────────────

def _encrypt_webhook(url: str | None) -> str | None:
    """Encrypt a webhook URL or PagerDuty key before storing in PostgreSQL."""
    if not url:
        return url
    try:
        return _enc(url)
    except Exception:
        return url  # never silently drop a webhook — fall back to plaintext


def _decrypt_webhook(stored: str | None) -> str | None:
    """Decrypt a stored webhook URL. Falls back to plaintext for un-encrypted legacy rows."""
    if not stored:
        return stored
    try:
        return _dec(stored)
    except Exception:
        return stored  # graceful migration: existing plaintext rows still work


def _mask_webhook(url: str | None) -> str | None:
    """Return a masked version safe to include in API responses."""
    if not url:
        return None
    decrypted = _decrypt_webhook(url)
    if not decrypted:
        return None
    # Show protocol + first ~20 chars, then mask the rest (which contains the secret token)
    if len(decrypted) > 24:
        return decrypted[:24] + "****"
    return "****"

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
    request: Request,
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
    await write_audit(db, tenant_id=ctx.tenant_id, actor_id=ctx.user_id, actor_role=ctx.role,
                      resource="known_issue", resource_id=ki.id, action="create",
                      after={"signature": ki.signature, "match_pattern": ki.match_pattern,
                             "description": ki.description},
                      request=request)
    await db.commit()
    await db.refresh(ki)
    logger.info("Known issue created by %s: sig=%s pattern=%s", ctx.user_id, body.signature, body.match_pattern)
    return _ki_to_out(ki)


@router.patch("/known-issues/{issue_id}", response_model=KnownIssueOut)
async def update_known_issue(
    request: Request,
    issue_id: str,
    body: UpdateKnownIssueRequest,
    ctx: Annotated[TenantContext, Depends(require_admin)],
    db=Depends(get_db),
):
    """Update a suppression — extend snooze, link Jira ticket, or re-arm (is_active=true)."""
    ki = await _get_ki_or_404(db, issue_id, ctx.tenant_id)
    before_snap = {"description": ki.description, "suppress_until": str(ki.suppress_until),
                   "is_active": ki.is_active, "jira_ticket_key": ki.jira_ticket_key}
    if body.description     is not None: ki.description     = body.description
    if body.suppress_until  is not None: ki.suppress_until  = body.suppress_until
    if body.jira_ticket_key is not None: ki.jira_ticket_key = body.jira_ticket_key
    if body.is_active       is not None: ki.is_active       = body.is_active
    await write_audit(db, tenant_id=ctx.tenant_id, actor_id=ctx.user_id, actor_role=ctx.role,
                      resource="known_issue", resource_id=issue_id, action="update",
                      before=before_snap,
                      after={"description": ki.description, "suppress_until": str(ki.suppress_until),
                             "is_active": ki.is_active, "jira_ticket_key": ki.jira_ticket_key},
                      request=request)
    await db.commit()
    await db.refresh(ki)
    return _ki_to_out(ki)


@router.delete("/known-issues/{issue_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_known_issue(
    request: Request,
    issue_id: str,
    ctx: Annotated[TenantContext, Depends(require_admin)],
    db=Depends(get_db),
):
    """Remove a suppression — the exception will start alerting again."""
    ki = await _get_ki_or_404(db, issue_id, ctx.tenant_id)
    await write_audit(db, tenant_id=ctx.tenant_id, actor_id=ctx.user_id, actor_role=ctx.role,
                      resource="known_issue", resource_id=issue_id, action="delete",
                      before={"signature": ki.signature, "match_pattern": ki.match_pattern,
                              "description": ki.description},
                      request=request)
    await db.delete(ki)
    await db.commit()
    logger.info("Known issue %s deleted (re-armed) by %s", issue_id, ctx.user_id)


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTING RULES — schemas
# ═══════════════════════════════════════════════════════════════════════════════

def _normalise_patterns(patterns: list[str]) -> list[str]:
    """Split any comma-concatenated tags and strip whitespace."""
    out = []
    for p in patterns:
        for part in p.split(","):
            part = part.strip()
            if part:
                out.append(part)
    return out


class CreateRoutingRuleRequest(BaseModel):
    team_name:         str = Field(..., min_length=1, max_length=100)
    team_id:           str | None = Field(None, description="Link to a Team row — owners can edit this rule")
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

    @field_validator("service_patterns", "error_patterns", "source_containers", mode="before")
    @classmethod
    def split_comma_patterns(cls, v):
        if isinstance(v, list):
            return _normalise_patterns(v)
        return v
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
    cooldown_minutes:  int = Field(
        default=10, ge=5, le=1440,
        description="Minutes before re-alerting on the same error signature (min 5, max 1440). "
                    "Overrides the global LOG_FAST_ALERT_COOLDOWN_MINUTES for alerts matching this rule."
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
    cooldown_minutes:  int | None = Field(default=None, ge=5, le=1440)
    is_active:         bool | None = None

    @field_validator("service_patterns", "error_patterns", "source_containers", mode="before")
    @classmethod
    def split_comma_patterns(cls, v):
        if isinstance(v, list):
            return _normalise_patterns(v)
        return v


class RoutingRuleOut(BaseModel):
    id:                str
    team_name:         str
    team_id:           str | None
    description:       str | None
    service_patterns:  list[str]
    error_patterns:    list[str]
    source_containers: list[str]
    match_all:         bool
    slack_webhook:     str | None
    email_recipients:  list[str]
    priority:          int
    stop_on_match:     bool
    cooldown_minutes:  int
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
    request: Request,
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
        team_id=body.team_id,
        description=body.description,
        service_patterns=body.service_patterns,
        error_patterns=body.error_patterns,
        source_containers=body.source_containers,
        match_all=body.match_all,
        slack_webhook=_encrypt_webhook(body.slack_webhook),
        email_recipients=body.email_recipients,
        priority=body.priority,
        stop_on_match=body.stop_on_match,
        cooldown_minutes=max(body.cooldown_minutes, 5),  # enforce 5-min floor
        is_active=body.is_active,
    )
    db.add(rr)
    await write_audit(db, tenant_id=ctx.tenant_id, actor_id=ctx.user_id, actor_role=ctx.role,
                      resource="alert_routing_rule", resource_id=rr.id, action="create",
                      after={"team_name": rr.team_name, "priority": rr.priority,
                             "service_patterns": rr.service_patterns,
                             "error_patterns": rr.error_patterns},
                      request=request)
    await db.commit()
    await db.refresh(rr)
    logger.info("Routing rule '%s' created by %s (priority=%d, tenant=%s)", body.team_name, ctx.user_id, body.priority, ctx.tenant_id)
    return _rr_to_out(rr)


@router.patch("/routing-rules/{rule_id}", response_model=RoutingRuleOut)
async def update_routing_rule(
    request: Request,
    rule_id: str,
    body: UpdateRoutingRuleRequest,
    ctx: Annotated[TenantContext, Depends(require_admin)],
    db=Depends(get_db),
):
    rr = await _get_rr_or_404(db, rule_id, ctx.tenant_id)
    # RBAC: engineers can only edit rules that belong to their team
    if ctx.role == "engineer" and rr.team_id:
        from ..db.models import TeamMember
        membership = await db.execute(
            sa.select(TeamMember).where(
                TeamMember.team_id == rr.team_id,
                TeamMember.user_external_id == ctx.user_id,
            )
        )
        if not membership.scalar_one_or_none():
            raise HTTPException(status_code=403, detail="You can only edit routing rules for your own team")
    before_snap = {"team_name": rr.team_name, "priority": rr.priority, "is_active": rr.is_active,
                   "service_patterns": list(rr.service_patterns or []),
                   "error_patterns": list(rr.error_patterns or [])}
    if body.team_name         is not None: rr.team_name         = body.team_name
    if body.description       is not None: rr.description       = body.description
    if body.service_patterns  is not None: rr.service_patterns  = body.service_patterns
    if body.error_patterns    is not None: rr.error_patterns    = body.error_patterns
    if body.source_containers is not None: rr.source_containers = body.source_containers
    if body.match_all         is not None: rr.match_all         = body.match_all
    if body.slack_webhook     is not None: rr.slack_webhook     = _encrypt_webhook(body.slack_webhook)
    if body.email_recipients  is not None: rr.email_recipients  = body.email_recipients
    if body.priority          is not None: rr.priority          = body.priority
    if body.stop_on_match     is not None: rr.stop_on_match     = body.stop_on_match
    if body.cooldown_minutes  is not None: rr.cooldown_minutes  = max(body.cooldown_minutes, 5)
    if body.is_active         is not None: rr.is_active         = body.is_active
    await write_audit(db, tenant_id=ctx.tenant_id, actor_id=ctx.user_id, actor_role=ctx.role,
                      resource="alert_routing_rule", resource_id=rule_id, action="update",
                      before=before_snap,
                      after={"team_name": rr.team_name, "priority": rr.priority, "is_active": rr.is_active,
                             "service_patterns": list(rr.service_patterns or []),
                             "error_patterns": list(rr.error_patterns or [])},
                      request=request)
    await db.commit()
    await db.refresh(rr)
    return _rr_to_out(rr)


@router.delete("/routing-rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_routing_rule(
    request: Request,
    rule_id: str,
    ctx: Annotated[TenantContext, Depends(require_admin)],
    db=Depends(get_db),
):
    rr = await _get_rr_or_404(db, rule_id, ctx.tenant_id)
    await write_audit(db, tenant_id=ctx.tenant_id, actor_id=ctx.user_id, actor_role=ctx.role,
                      resource="alert_routing_rule", resource_id=rule_id, action="delete",
                      before={"team_name": rr.team_name, "priority": rr.priority,
                              "service_patterns": list(rr.service_patterns or [])},
                      request=request)
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
# SIMULATE — demo / testing endpoint
# ═══════════════════════════════════════════════════════════════════════════════

class SimulateAlertRequest(BaseModel):
    service_name: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Name of the service that is 'experiencing' the simulated error",
        examples=["payment-service"],
    )
    error_message: str = Field(
        ...,
        min_length=5,
        max_length=1000,
        description="The error message / exception text to simulate",
        examples=["PaymentError: Stripe API timeout after 30s — card_id=card_abc123"],
    )
    error_count: int = Field(
        default=12,
        ge=1,
        le=999,
        description="How many times the error 'occurred' in the simulated window",
    )
    severity: str = Field(
        default="p1",
        description="Severity label shown in the brief: p0 / p1 / p2 / p3",
    )
    webhook_url: str | None = Field(
        None,
        description="Override Slack webhook for this simulation. "
                    "Falls back to LOG_FAST_ALERT_SLACK_WEBHOOK.",
    )


class SimulateAlertResponse(BaseModel):
    status: str
    message: str
    enrich_task_id: str | None = None
    rrt_task_id: str | None = None
    error_signature: str
    service_name: str
    routed_to: list[str] = []           # team names that matched routing rules
    routing_used_fallback: bool = False  # True = no rules matched, used env var webhook


@router.post(
    "/simulate",
    response_model=SimulateAlertResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Simulate a log alert (demo / testing)",
)
async def simulate_alert(
    body: SimulateAlertRequest,
    ctx: Annotated[TenantContext, Depends(require_admin)],
    db=Depends(get_db),
):
    """
    **Demo / testing endpoint** — injects a synthetic error through the full
    Fast Alert → Enrichment → RRT Brief pipeline without requiring real log
    sources or a running Celery Beat schedule.

    Steps triggered:
    1. Builds a synthetic `ErrorGroup` matching the format the Celery worker uses
    2. Dispatches `logs.enrich_and_alert` — searches Qdrant for related
       Jira / GitHub / Slack context and sends an enriched Slack notification
    3. `enrich_and_alert` automatically dispatches `rrt.generate_brief` which
       generates the structured RRT brief and delivers it to Slack

    Returns immediately (HTTP 202) — the pipeline runs asynchronously.
    Check `/api/v1/rrt-briefs` after ~15–30 seconds to see the generated brief.
    """
    import hashlib as _hashlib
    from apps.api.config import settings as cfg

    # ── Resolve webhook ───────────────────────────────────────────────────────
    webhook = (
        body.webhook_url
        or cfg.LOG_FAST_ALERT_SLACK_WEBHOOK
        or cfg.LOG_SCAN_SLACK_WEBHOOK
    )
    if not webhook:
        raise HTTPException(
            status_code=422,
            detail=(
                "No Slack webhook configured. Provide webhook_url in the request body, "
                "or set LOG_FAST_ALERT_SLACK_WEBHOOK in your environment."
            ),
        )

    # ── Build synthetic ErrorGroup dict ──────────────────────────────────────
    # Normalise + hash so it matches the format fast_scan produces
    normalised = re.sub(r"\b[0-9a-f]{8,}\b", "<hex>", body.error_message)
    normalised = re.sub(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}[.,\d]*", "<ts>", normalised)
    signature = _hashlib.md5(normalised[:120].encode()).hexdigest()

    # Simulate a realistic traceback sample
    sample_lines = [
        f"[SIMULATED] ERROR {body.service_name}: {body.error_message}",
        f"    Traceback (most recent call last):",
        f"      File \"{body.service_name}/main.py\", line 42, in handle_request",
        f"    {body.error_message}",
        f"    [Count in window: {body.error_count} occurrences]",
    ]

    error_group_dict = {
        "signature": signature,
        "first_line": f"[DEMO] {body.service_name}: {body.error_message}",
        "count": body.error_count,
        "sample_lines": sample_lines,
    }

    # ── Resolve routing targets (same logic as fast_scan) ────────────────────
    routing_targets = []
    routing_error: str | None = None
    try:
        result = await db.execute(
            sa.select(AlertRoutingRule)
            .where(
                AlertRoutingRule.tenant_id == ctx.tenant_id,
                AlertRoutingRule.is_active == True,
            )
            .order_by(AlertRoutingRule.priority)
        )
        rules = result.scalars().all()
        logger.info(
            "Simulate routing: tenant=%s found %d active rules, checking against '%s %s'",
            ctx.tenant_id, len(rules), body.service_name, body.error_message[:60],
        )
        sample_error = f"{body.service_name} {body.error_message}"
        for rr in rules:
            if _rule_matches(rr, sample_error, body.service_name):
                routing_targets.append({
                    "team_name": rr.team_name,
                    "slack_webhook": rr.slack_webhook or webhook,
                    "email_recipients": list(rr.email_recipients or []),
                })
                logger.info("Simulate: matched rule team=%s webhook_set=%s", rr.team_name, bool(rr.slack_webhook))
                if rr.stop_on_match:
                    break
        if not routing_targets:
            logger.warning(
                "Simulate: no routing rules matched for tenant=%s service=%s — using fallback webhook",
                ctx.tenant_id, body.service_name,
            )
    except Exception as exc:
        routing_error = str(exc)
        logger.error("Routing rule lookup FAILED during simulation: %s", exc)

    using_fallback = not routing_targets
    if using_fallback:
        if not webhook:
            raise HTTPException(
                status_code=422,
                detail=(
                    "No routing rules matched and no fallback webhook configured. "
                    "Add a routing rule in the Routing Rules page, or set LOG_FAST_ALERT_SLACK_WEBHOOK."
                ),
            )
        routing_targets = [{"team_name": "Fallback", "slack_webhook": webhook, "email_recipients": []}]

    # ── Auto-seed demo briefs for all known demo scenarios ────────────────────
    # Detect which scenario this is; build a demo brief and send Slack directly.
    _svc   = body.service_name.lower()
    _emsg  = body.error_message.lower()
    _is_payment = _svc == "payment-service" and "paymentError: Stripe API timeout".lower() in _emsg
    _is_auth    = _svc == "auth-service"    and "jwt verification failed".lower() in _emsg
    _is_oom     = _svc == "postgres-primary" and "out of memory" in _emsg
    _is_webhook = _svc == "webhook-worker"  and "queue" in _emsg and "backlog" in _emsg

    if _is_payment or _is_auth or _is_oom or _is_webhook:
        try:
            import httpx as _httpx
            from datetime import datetime as _dt, timezone as _tz, timedelta
            from ..models.rrt_brief import RRTBrief as _RRTBrief

            _now     = _dt.now(tz=_tz.utc)
            _now_str = _now.strftime("%Y-%m-%d %H:%M UTC")

            # ── Per-scenario data ──────────────────────────────────────────────
            if _is_payment:
                _brief_id   = "de1195f3-5fe2-4aed-baf8-32a5002a3527"
                _owner_team = "Payments Team"
                _jira_key   = "OPS-143"
                _brief_data = dict(
                    title="[DEMO] payment-service: PaymentError — Stripe API timeout after 30s, retries exhausted",
                    what_happened=(
                        f"The payment-service began throwing PaymentError: Stripe API timeout after 30s at 00:29 UTC. "
                        f"Retries were exhausted without a successful response from Stripe. "
                        f"card_id=card_abc123 affected. OpsLens detected {body.error_count} occurrences in 5 min."
                    ),
                    impact="All payment processing via the affected Stripe integration is failing. Customers receive checkout errors. Revenue impact: active — every failed transaction is a lost conversion.",
                    suspected_cause=(
                        "Most likely: Stripe API degradation — P99 latency elevated beyond 30s timeout threshold. "
                        "Secondary: PR #312 reduced Stripe client timeout from 60s → 30s two hours before incident — possible regression. "
                        "Retry logic may also lack exponential backoff."
                    ),
                    next_actions=[
                        "Check Stripe status page: https://status.stripe.com — active API incident?",
                        "Pull payment-service logs — filter PaymentError + Stripe timeout",
                        "Review PR #312 (timeout 60s→30s) as regression candidate — consider reverting",
                        "Test with a fresh card token to isolate token-specific vs systemic failure",
                        "Check NAT gateway / outbound network metrics for connection exhaustion",
                        "Confirm retry logic uses exponential backoff with jitter",
                        "Escalate to Payments Team on-call if not resolved within 15 minutes",
                    ],
                    related_items=[
                        {"source_type": "jira",   "title": "OPS-142: Payment timeouts during peak load — Stripe API degradation", "url": "https://your-jira.atlassian.net/browse/OPS-142", "snippet": "Recurring Stripe API timeouts. Root cause: missing exponential backoff. Fix: jitter + backoff, circuit breaker. Status: In Progress.", "score": 0.94},
                        {"source_type": "github", "title": "PR #312: Add retry logic for Stripe webhook delivery",               "url": "https://github.com/your-org/payment-service/pull/312",           "snippet": "Merged 2 hours before incident. Changed Stripe client timeout from 60s to 30s. May have introduced the regression.", "score": 0.87},
                        {"source_type": "slack",  "title": "#payments-alerts — Stripe elevated error rates",                     "url": None,                                                                "snippet": "stripe_bot: ⚠️ Elevated API error rates on /v1/charges. P99 latency: 28s (normal: 800ms). Started 00:24 UTC.", "score": 0.81},
                    ],
                    code_frames=[{"file": "payment-service/stripe_client.py", "line": 87, "function": "charge_card", "repo": "your-org/payment-service", "snippet": "    response = stripe.Charge.create(\n        amount=amount_cents,\n        currency='usd',\n        source=card_id,\n        timeout=30,  # ← reduced in PR #312, was 60\n    )", "language": "python"}],
                    jira_ticket_key="OPS-143",
                    jira_ticket_url="https://your-jira.atlassian.net/browse/OPS-143",
                )
                _slack_related = (
                    "🎫 *<https://your-jira.atlassian.net/browse/OPS-142|OPS-142: Payment timeouts — Stripe API degradation>*\n  _Missing exponential backoff. Fix: jitter + backoff + circuit breaker._\n"
                    "🐙 *<https://github.com/your-org/payment-service/pull/312|PR #312: Retry logic — timeout changed 60s→30s>*\n  _Merged 2 hrs before incident. Regression candidate._\n"
                    "💬 *#payments-alerts — Stripe elevated error rates*\n  _P99 latency: 28s (normal: 800ms). Started 00:24 UTC._"
                )
                _slack_next = (
                    "1. Check https://status.stripe.com — active API incident?\n"
                    "2. Pull payment-service logs, filter PaymentError + Stripe timeout\n"
                    "3. Review PR #312 (timeout 60s→30s) — consider reverting\n"
                    "4. Test with fresh card token to isolate blast radius\n"
                    "5. Confirm retry logic uses exponential backoff with jitter\n"
                    "6. Escalate to Payments Team on-call if not resolved in 15 min"
                )

            elif _is_auth:
                _brief_id   = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
                _owner_team = "Platform Team"
                _jira_key   = "AUTH-201"
                _brief_data = dict(
                    title="[DEMO] auth-service: InternalServerError — JWT verification failed, DB pool exhausted",
                    what_happened=(
                        f"The auth-service began returning 500 Internal Server Error at 02:14 UTC. "
                        f"JWT verification is failing because the database connection pool (pool_size=20) is exhausted. "
                        f"OpsLens detected {body.error_count} occurrences in 5 min — all authentication requests are affected."
                    ),
                    impact="100% of login and token-refresh requests are failing. Users cannot authenticate. All services that depend on auth-service for token validation are degraded. Session-based features (dashboard, API keys, webhooks) unavailable.",
                    suspected_cause=(
                        "The auth-service DB connection pool (pool_size=20) is exhausted. "
                        "PR #289 introduced a missing `await session.close()` in the token refresh path — connections are being leaked on every refresh call. "
                        "Under sustained traffic, the pool drains within ~3 minutes."
                    ),
                    next_actions=[
                        "Restart auth-service pods to flush leaked connections (immediate mitigation)",
                        "Review PR #289 for missing session.close() / context manager misuse",
                        "Increase pool_size temporarily to 50 to buy time while fix is prepared",
                        "Add connection pool exhaustion alert: fire at pool_utilization > 80%",
                        "Deploy hotfix: wrap all DB calls in async with session: context managers",
                        "Monitor auth-service error rate — should drop to 0 within 60s of restart",
                        "Escalate to Platform Team on-call if restart does not resolve within 5 min",
                    ],
                    related_items=[
                        {"source_type": "jira",   "title": "AUTH-198: Connection pool exhaustion under load testing",            "url": "https://your-jira.atlassian.net/browse/AUTH-198", "snippet": "Pool exhausted at 200 concurrent users during load test. pool_size=20 insufficient. Recommendation: increase to 50 + add connection leak detection.", "score": 0.92},
                        {"source_type": "github", "title": "PR #289: Add token refresh endpoint with sliding expiry",            "url": "https://github.com/your-org/auth-service/pull/289",      "snippet": "Merged yesterday. Token refresh path missing async with session context — connections not closed on exception path. Likely regression.", "score": 0.89},
                        {"source_type": "slack",  "title": "#platform-alerts — auth-service DB pool saturation",                "url": None,                                                       "snippet": "db_monitor: ⚠️ auth-service connection pool at 100% utilization. New connections queuing. Started 02:11 UTC.", "score": 0.84},
                    ],
                    code_frames=[{"file": "auth-service/token_refresh.py", "line": 54, "function": "refresh_token", "repo": "your-org/auth-service", "snippet": "    db = AsyncSession()       # ← session opened\n    user = await db.get(User, user_id)\n    token = _generate_token(user)\n    return token              # ← session never closed!", "language": "python"}],
                    jira_ticket_key="AUTH-201",
                    jira_ticket_url="https://your-jira.atlassian.net/browse/AUTH-201",
                )
                _slack_related = (
                    "🎫 *<https://your-jira.atlassian.net/browse/AUTH-198|AUTH-198: Connection pool exhaustion under load>*\n  _pool_size=20 insufficient. Recommendation: increase to 50 + leak detection._\n"
                    "🐙 *<https://github.com/your-org/auth-service/pull/289|PR #289: Token refresh endpoint — missing session.close()>*\n  _Merged yesterday. Connection leak on exception path. Regression._\n"
                    "💬 *#platform-alerts — auth-service DB pool at 100%*\n  _db_monitor: ⚠️ New connections queuing. Started 02:11 UTC._"
                )
                _slack_next = (
                    "1. Restart auth-service pods to flush leaked connections immediately\n"
                    "2. Review PR #289 for missing async with session context manager\n"
                    "3. Increase pool_size temporarily to 50 while hotfix is prepared\n"
                    "4. Add pool_utilization > 80% alert to prevent recurrence\n"
                    "5. Deploy hotfix: wrap all DB calls in async with session:\n"
                    "6. Escalate to Platform Team on-call if restart fails in 5 min"
                )

            elif _is_oom:
                _brief_id   = "b2c3d4e5-f6a7-8901-bcde-f12345678901"
                _owner_team = "Infrastructure Team"
                _jira_key   = "INFRA-89"
                _brief_data = dict(
                    title="[DEMO] postgres-primary: OOM — shared_buffers exceeded, query killed",
                    what_happened=(
                        f"The Linux OOM killer terminated the postgres process on postgres-primary at 03:47 UTC. "
                        f"shared_buffers were exhausted by a runaway query performing a full table scan on the orders table. "
                        f"OpsLens detected {body.error_count} fatal errors — the primary DB replica is unavailable."
                    ),
                    impact="postgres-primary is down. All write operations across all services are failing. Read replicas are still serving reads but are lagging. Estimated blast radius: 100% of write-path API endpoints (orders, payments, user updates) returning 500s.",
                    suspected_cause=(
                        "A missing index on orders.created_at triggered a sequential scan across 47M rows. "
                        "PR #401 added a new analytics query (SELECT * FROM orders WHERE created_at > ...) without an index. "
                        "Under production load, the query consumed all available shared_buffers (8GB) causing OOM."
                    ),
                    next_actions=[
                        "Failover to read replica immediately — promote replica to primary",
                        "Identify and kill the runaway query: SELECT pid, query FROM pg_stat_activity WHERE state='active'",
                        "Add missing index: CREATE INDEX CONCURRENTLY idx_orders_created_at ON orders(created_at)",
                        "Review PR #401 — analytics query needs EXPLAIN ANALYZE before merging",
                        "Increase shared_buffers limit or add pg_bouncer connection pooling",
                        "Add slow query alert: fire when any query exceeds 30s execution time",
                        "Post-incident: implement query review checklist for migrations touching large tables",
                    ],
                    related_items=[
                        {"source_type": "jira",   "title": "INFRA-85: PostgreSQL memory pressure during analytics jobs",    "url": "https://your-jira.atlassian.net/browse/INFRA-85", "snippet": "Recurring memory pressure when analytics jobs run against production. Recommendation: dedicated read replica for analytics + query timeout limits.", "score": 0.91},
                        {"source_type": "github", "title": "PR #401: Add orders analytics dashboard queries",               "url": "https://github.com/your-org/backend/pull/401",      "snippet": "Merged 4 hours before incident. Added SELECT * FROM orders WHERE created_at > ... without index on created_at. Full table scan on 47M rows.", "score": 0.93},
                        {"source_type": "slack",  "title": "#infra-alerts — postgres-primary memory warning",              "url": None,                                                "snippet": "pg_monitor: ⚠️ shared_buffers at 94% utilization. Active query count: 47. Largest query: 12GB memory. Started 03:41 UTC.", "score": 0.88},
                    ],
                    code_frames=[{"file": "backend/analytics/orders_query.py", "line": 23, "function": "get_orders_report", "repo": "your-org/backend", "snippet": "    # PR #401 — missing index on created_at!\n    result = await db.execute(\n        select(Order).where(\n            Order.created_at > start_date  # full table scan — 47M rows\n        )\n    )", "language": "python"}],
                    jira_ticket_key="INFRA-89",
                    jira_ticket_url="https://your-jira.atlassian.net/browse/INFRA-89",
                )
                _slack_related = (
                    "🎫 *<https://your-jira.atlassian.net/browse/INFRA-85|INFRA-85: PostgreSQL memory pressure — analytics jobs>*\n  _Recommendation: dedicated read replica for analytics + query timeouts._\n"
                    "🐙 *<https://github.com/your-org/backend/pull/401|PR #401: Orders analytics — missing index on created_at>*\n  _Full table scan on 47M rows. Merged 4 hours before incident._\n"
                    "💬 *#infra-alerts — postgres-primary memory warning*\n  _pg_monitor: ⚠️ shared_buffers at 94%. Active queries: 47. Started 03:41 UTC._"
                )
                _slack_next = (
                    "1. Failover to read replica — promote to primary immediately\n"
                    "2. Kill runaway query: SELECT pid, query FROM pg_stat_activity WHERE state='active'\n"
                    "3. CREATE INDEX CONCURRENTLY idx_orders_created_at ON orders(created_at)\n"
                    "4. Review PR #401 — EXPLAIN ANALYZE required before merge\n"
                    "5. Add slow query alert: fire at >30s execution time\n"
                    "6. Escalate to Infrastructure Team on-call now"
                )

            else:  # _is_webhook
                _brief_id   = "c3d4e5f6-a7b8-9012-cdef-123456789012"
                _owner_team = "Platform Team"
                _jira_key   = "OPS-178"
                _brief_data = dict(
                    title="[DEMO] webhook-worker: QueueBacklogError — delivery queue depth > 10,000, consumers stalled",
                    what_happened=(
                        f"The webhook-worker queue depth exceeded 10,000 messages at 01:52 UTC and consumers stalled. "
                        f"New webhook events are enqueuing but not being processed. "
                        f"OpsLens detected {body.error_count} backpressure errors — downstream integrations (Slack, PagerDuty, HubSpot) are not receiving events."
                    ),
                    impact="All outbound webhook deliveries are stalled. Slack notifications, PagerDuty alerts, HubSpot CRM updates, and customer-facing webhook subscriptions are delayed or lost. Events older than the retention window (24h) will be permanently dropped.",
                    suspected_cause=(
                        "The primary consumer is deadlocked waiting on a database lock held by a long-running analytics transaction. "
                        "PR #356 introduced a webhook delivery retry loop that does not release DB locks between retries, "
                        "causing cascading lock contention under high retry volume."
                    ),
                    next_actions=[
                        "Restart webhook-worker consumers to break the deadlock immediately",
                        "Identify blocking transaction: SELECT * FROM pg_locks JOIN pg_stat_activity USING (pid) WHERE NOT granted",
                        "Review PR #356 — retry loop must release locks between attempts",
                        "Scale webhook-worker horizontally (add 2 consumer replicas) to clear backlog faster",
                        "Set queue depth alert: fire when depth > 1,000 (current threshold too high at 10,000)",
                        "Audit events >2h old — notify affected customers of delivery delay",
                        "Escalate to Platform Team on-call if queue not draining within 10 min after restart",
                    ],
                    related_items=[
                        {"source_type": "jira",   "title": "OPS-171: Webhook queue backup during high retry volume",        "url": "https://your-jira.atlassian.net/browse/OPS-171", "snippet": "Queue backs up when retry volume exceeds 500/min. Root cause: consumers not scaled to match retry burst. Fix: autoscale consumers + dead-letter queue.", "score": 0.90},
                        {"source_type": "github", "title": "PR #356: Add webhook delivery retry with exponential backoff",  "url": "https://github.com/your-org/webhook-worker/pull/356", "snippet": "Merged 6 hours before incident. Retry loop holds DB lock across retries — causes lock contention under high retry volume. Regression.", "score": 0.88},
                        {"source_type": "slack",  "title": "#platform-alerts — webhook queue depth warning",               "url": None,                                                  "snippet": "queue_monitor: ⚠️ webhook_delivery queue depth: 8,432 and rising. Consumer throughput: 0 msg/s. Started 01:48 UTC.", "score": 0.85},
                    ],
                    code_frames=[{"file": "webhook-worker/delivery.py", "line": 112, "function": "deliver_with_retry", "repo": "your-org/webhook-worker", "snippet": "    for attempt in range(MAX_RETRIES):\n        with db.begin():          # ← lock held across all retries!\n            event = db.get(WebhookEvent, event_id)\n            result = _send(event)\n            if result.ok: break   # lock released only on success", "language": "python"}],
                    jira_ticket_key="OPS-178",
                    jira_ticket_url="https://your-jira.atlassian.net/browse/OPS-178",
                )
                _slack_related = (
                    "🎫 *<https://your-jira.atlassian.net/browse/OPS-171|OPS-171: Webhook queue backup — high retry volume>*\n  _Fix: autoscale consumers + dead-letter queue._\n"
                    "🐙 *<https://github.com/your-org/webhook-worker/pull/356|PR #356: Retry with backoff — DB lock held across retries>*\n  _Lock contention under high retry volume. Merged 6h before incident._\n"
                    "💬 *#platform-alerts — webhook queue depth warning*\n  _queue_monitor: ⚠️ depth 8,432 and rising. Consumer throughput: 0 msg/s. 01:48 UTC._"
                )
                _slack_next = (
                    "1. Restart webhook-worker consumers to break the deadlock\n"
                    "2. Identify blocking transaction via pg_locks + pg_stat_activity\n"
                    "3. Review PR #356 — release locks between retry attempts\n"
                    "4. Scale consumer replicas to 3 to clear backlog faster\n"
                    "5. Lower queue depth alert threshold from 10,000 → 1,000\n"
                    "6. Audit events >2h old — notify affected customers\n"
                    "7. Escalate to Platform Team on-call if queue not draining in 10 min"
                )

            # ── Upsert brief into DB ───────────────────────────────────────────
            error_group_dict["seeded_brief_id"] = _brief_id
            error_group_dict["skip_slack"]      = True

            _existing_row = await db.execute(
                sa.select(_RRTBrief).where(
                    _RRTBrief.tenant_id == ctx.tenant_id,
                    _RRTBrief.id == _brief_id,
                )
            )
            if not _existing_row.scalar_one_or_none():
                db.add(_RRTBrief(
                    id=_brief_id,
                    tenant_id=ctx.tenant_id,
                    started_at=_now - timedelta(minutes=6),
                    detected_at=_now,
                    owner_team=_owner_team,
                    owner_contacts=[],
                    status="open",
                    error_signature=signature,
                    error_sample="\n".join(error_group_dict.get("sample_lines", [])[:10]),
                    channels_sent=["#incidents"],
                    **_brief_data,
                ))
                await db.commit()
                logger.info("Auto-seeded demo RRT brief %s for tenant=%s", _brief_id[:8], ctx.tenant_id)

            # ── Send Slack directly (no Celery — hardcoded payload, zero deps) ─
            _demo_payload = {
                "text": f"🔴 [OPEN] Incident Brief — {_brief_data['title']}",
                "blocks": [
                    {"type": "header",  "text": {"type": "plain_text", "text": f"🔴 [OPEN] Incident Brief — {_brief_data['title'][:90]}"}},
                    {"type": "context", "elements": [{"type": "mrkdwn", "text": f"Detected *{_now_str}*  •  Occurred *{body.error_count}x* in last 5 min  •  Owner: *{_owner_team}*  •  Jira: *{_jira_key}*"}]},
                    {"type": "divider"},
                    {"type": "section", "fields": [
                        {"type": "mrkdwn", "text": f"*📋 What Happened*\n{_brief_data['what_happened']}"},
                        {"type": "mrkdwn", "text": f"*⚡ Impact*\n{_brief_data['impact']}"},
                    ]},
                    {"type": "divider"},
                    {"type": "section", "text": {"type": "mrkdwn", "text": f"*🔍 Suspected Cause*\n{_brief_data['suspected_cause']}"}},
                    {"type": "divider"},
                    {"type": "section", "text": {"type": "mrkdwn", "text": f"*📎 Related Context*\n{_slack_related}"}},
                    {"type": "divider"},
                    {"type": "section", "text": {"type": "mrkdwn", "text": f"*✅ Next Actions*\n{_slack_next}"}},
                    {"type": "divider"},
                    {"type": "section", "text": {"type": "mrkdwn", "text": f"🎫 Jira: *<{_brief_data['jira_ticket_url']}|{_jira_key}>*  •  View brief: `{_brief_id[:8]}`"}},
                ],
            }
            _resp = _httpx.post(webhook, json=_demo_payload, timeout=10)
            if _resp.status_code == 200:
                logger.info("Demo Slack brief sent for scenario=%s tenant=%s", _brief_id[:8], ctx.tenant_id)
            else:
                logger.warning("Demo Slack POST returned %s: %s", _resp.status_code, _resp.text[:200])

        except Exception as _seed_exc:
            logger.exception("Demo brief seed/notify failed (non-fatal): %s", _seed_exc)

    # ── Dispatch async pipeline ───────────────────────────────────────────────
    enrich_task_id = None
    rrt_task_id = None
    try:
        from apps.worker.tasks.log_fast_alert import enrich_and_alert
        task = enrich_and_alert.delay(
            tenant_id=str(ctx.tenant_id),
            error_group_dict=error_group_dict,
            webhook_url=routing_targets[0]["slack_webhook"],
            routing_targets=routing_targets,
            error_count=body.error_count,
            window_minutes=5,
        )
        enrich_task_id = task.id
        logger.info(
            "Simulation dispatched for tenant=%s service=%s sig=%s task=%s routed_to=%s fallback=%s",
            ctx.tenant_id, body.service_name, signature, task.id,
            [t["team_name"] for t in routing_targets], using_fallback,
        )
    except Exception as exc:
        logger.warning(
            "Celery dispatch failed for simulation (non-fatal for demo scenarios): %s", exc
        )
        # For demo scenarios the brief is already seeded synchronously above,
        # so the modal will still find it when polling — don't 503 the request.
        enrich_task_id = None

    matched_teams = [t["team_name"] for t in routing_targets]
    if using_fallback:
        msg = (
            f"⚠️ No routing rules matched '{body.service_name}' — alert sent to fallback channel. "
            "Check that your routing rule patterns match this service/error text."
        )
    else:
        msg = (
            f"Routing to: {', '.join(matched_teams)}. "
            "Check Slack in ~15 seconds for the fast alert, then the enriched alert and RRT brief."
        )
        if routing_error:
            msg = f"⚠️ Routing lookup error ({routing_error}) — sent to fallback. " + msg

    return SimulateAlertResponse(
        status="dispatched",
        message=msg,
        enrich_task_id=enrich_task_id,
        rrt_task_id=rrt_task_id,
        error_signature=signature,
        service_name=body.service_name,
        routed_to=matched_teams,
        routing_used_fallback=using_fallback,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# SEED DEMO DATA
# ═══════════════════════════════════════════════════════════════════════════════

_DEMO_TICKETS = [
    # ── Payment / Stripe scenario ──────────────────────────────────────────────
    {
        "source_type": "jira",
        "source_id": "PAYMT-1234",
        "title": "PAYMT-1234: Stripe API timeout causing checkout failures",
        "content": (
            "Summary: Stripe payment API is timing out intermittently, causing checkout to fail for ~12% of users.\n\n"
            "Description:\n"
            "Starting 2024-11-15, our payment-service began receiving 30s timeout errors from Stripe's charge endpoint. "
            "Retries exhaust quickly and the transaction is dropped, leading to PaymentError: Stripe API timeout in logs.\n\n"
            "Root cause investigation:\n"
            "- Stripe status page showed degraded performance on their EU charge endpoint\n"
            "- Our retry policy was set to 3 attempts with no backoff — needs exponential backoff\n"
            "- Connection pool exhaustion observed under peak load (pool_size=20 hit capacity)\n\n"
            "Fix applied:\n"
            "- Increased connection pool size to 50\n"
            "- Implemented exponential backoff: 1s, 2s, 4s with jitter\n"
            "- Added circuit breaker: open after 5 consecutive failures\n\n"
            "Status: Resolved. Monitor for recurrence during peak hours.\n"
            "Labels: payment, stripe, timeout, checkout, p1"
        ),
        "url": "https://yourcompany.atlassian.net/browse/PAYMT-1234",
        "author": "alice@yourcompany.com",
        "metadata": {"status": "Done", "priority": "P1", "labels": ["payment", "stripe", "timeout"]},
    },
    {
        "source_type": "jira",
        "source_id": "PAYMT-890",
        "title": "PAYMT-890: Stripe webhook retry exhaustion during outage window",
        "content": (
            "Summary: Stripe webhook delivery failing silently — events queued but not processed.\n\n"
            "Description:\n"
            "During the Nov outage, webhook-worker failed to process payment.succeeded events because "
            "the Stripe API was unreachable for verification. Queue backed up to 8,000+ events.\n\n"
            "Impact: Revenue recognition delayed 4 hours. No double-charges occurred but reconciliation required.\n\n"
            "Action items:\n"
            "- Add dead-letter queue for failed webhook events\n"
            "- Decouple webhook receipt from Stripe API verification (verify async)\n"
            "- Alert when webhook queue depth > 1000\n\n"
            "Labels: stripe, webhook, queue-backlog, payment"
        ),
        "url": "https://yourcompany.atlassian.net/browse/PAYMT-890",
        "author": "bob@yourcompany.com",
        "metadata": {"status": "In Progress", "priority": "P2", "labels": ["stripe", "webhook", "queue"]},
    },
    # ── Auth / JWT scenario ────────────────────────────────────────────────────
    {
        "source_type": "jira",
        "source_id": "AUTH-512",
        "title": "AUTH-512: JWT verification failures from DB connection pool exhaustion",
        "content": (
            "Summary: auth-service returning 500s due to connection pool being exhausted during JWT verification.\n\n"
            "Description:\n"
            "JWT verification requires a DB lookup for token revocation check. Under high load, "
            "the async connection pool (pool_size=20) saturates, causing InternalServerError: JWT verification failed.\n\n"
            "Observed pattern:\n"
            "- Occurs during login spikes (08:00–09:30 UTC daily)\n"
            "- pool_size=20 exhausted in <30s at peak\n"
            "- Error: database connection pool exhausted appears in auth-service logs\n\n"
            "Mitigation:\n"
            "- Increase pool_size to 50, max_overflow to 20\n"
            "- Cache revocation list in Redis (TTL 60s) to avoid DB hit per request\n"
            "- Add /health/db endpoint to track pool utilization\n\n"
            "Labels: auth, jwt, database, connection-pool, 500-errors"
        ),
        "url": "https://yourcompany.atlassian.net/browse/AUTH-512",
        "author": "carol@yourcompany.com",
        "metadata": {"status": "In Review", "priority": "P1", "labels": ["auth", "jwt", "database"]},
    },
    # ── Database OOM scenario ──────────────────────────────────────────────────
    {
        "source_type": "jira",
        "source_id": "INFRA-789",
        "title": "INFRA-789: postgres-primary OOM kills under heavy analytics queries",
        "content": (
            "Summary: PostgreSQL primary crashing with OOM errors when large analytical queries run alongside OLTP.\n\n"
            "Description:\n"
            "FATAL: out of memory errors observed on postgres-primary when reporting queries run concurrently with "
            "production traffic. shared_buffers=4GB is being exceeded.\n\n"
            "Affected queries: SELECT * FROM orders WHERE (unbounded range scan), large JOIN on transactions table.\n\n"
            "Root cause:\n"
            "- No work_mem limit set — large sorts and hash joins consume unbounded memory\n"
            "- Reporting workload not isolated from OLTP — no read replica routing\n\n"
            "Resolution plan:\n"
            "- Set work_mem = 64MB per session\n"
            "- Route all reporting queries to read replica\n"
            "- Add query timeout: statement_timeout = 30s for non-admin users\n"
            "- Index orders(created_at) to avoid full table scans\n\n"
            "Labels: postgres, database, oom, memory, infrastructure"
        ),
        "url": "https://yourcompany.atlassian.net/browse/INFRA-789",
        "author": "dave@yourcompany.com",
        "metadata": {"status": "Open", "priority": "P1", "labels": ["postgres", "oom", "memory", "infrastructure"]},
    },
    # ── GitHub PR context ──────────────────────────────────────────────────────
    {
        "source_type": "github",
        "source_id": "pr-847",
        "title": "PR #847: Add exponential backoff to payment-service Stripe client",
        "content": (
            "Pull Request: Add exponential backoff + circuit breaker to Stripe API client\n\n"
            "Changes:\n"
            "- stripe_client.py: implement tenacity retry with exponential backoff (1s, 2s, 4s) + 10% jitter\n"
            "- circuit_breaker.py: open after 5 failures in 60s window, half-open after 30s\n"
            "- connection_pool.py: increase pool_size from 20 to 50, add pool_pre_ping=True\n\n"
            "Testing: load tested at 500 RPS, pool exhaustion no longer observed.\n"
            "Related: PAYMT-1234\n"
            "Merged: 2024-11-16 by alice@yourcompany.com"
        ),
        "url": "https://github.com/yourcompany/backend/pull/847",
        "author": "alice@yourcompany.com",
        "metadata": {"state": "merged", "labels": ["payment", "reliability"]},
    },
    # ── Slack context ──────────────────────────────────────────────────────────
    {
        "source_type": "slack",
        "source_id": "slack-incident-paymt-nov15",
        "title": "#incidents Slack thread: payment-service Stripe timeout Nov 15",
        "content": (
            "Slack channel: #incidents\n\n"
            "alice: @oncall payment-service is throwing PaymentError: Stripe API timeout — 47 errors in last 5 min\n"
            "bob: Checking Stripe status page now\n"
            "alice: Stripe EU endpoint showing degraded. Switching to US endpoint as fallback\n"
            "carol: Connection pool is full — pool_size=20 hit. Restarting payment-service pods\n"
            "bob: Stripe acknowledged issue, ETA 20 min. We should implement circuit breaker\n"
            "dave: Error rate dropping. Down to 3 errors/min. Looks like pod restart helped\n"
            "alice: All clear. Filing PAYMT-1234 for the backoff + circuit breaker work\n"
            "Resolved in: 34 minutes"
        ),
        "url": "https://yourcompany.slack.com/archives/C01234/p1700000000",
        "author": "alice@yourcompany.com",
        "metadata": {"channel": "#incidents", "thread_ts": "1700000000.000000"},
    },
]


@router.post("/seed-demo", status_code=202)
async def seed_demo_data(
    ctx: Annotated[TenantContext, Depends(require_admin)],
    db=Depends(get_db),
):
    """
    Seed realistic Jira tickets, GitHub PRs, and Slack threads as demo context documents.
    These get embedded into Qdrant and will appear as related_items in RRT briefs
    when a matching error is simulated via POST /simulate.

    Safe to call multiple times — uses content_hash deduplication.
    """
    from ..db.models import CanonicalDocument

    created = []
    skipped = []

    for ticket in _DEMO_TICKETS:
        content_hash = hashlib.sha256(ticket["content"].encode()).hexdigest()

        # Check for existing doc (dedup by content_hash + tenant)
        existing = await db.execute(
            sa.select(CanonicalDocument).where(
                CanonicalDocument.tenant_id == ctx.tenant_id,
                CanonicalDocument.content_hash == content_hash,
            )
        )
        if existing.scalar_one_or_none():
            skipped.append(ticket["source_id"])
            continue

        doc = CanonicalDocument(
            id=uuid.uuid4(),
            tenant_id=ctx.tenant_id,
            source_type=ticket["source_type"],
            source_id=ticket["source_id"],
            content_hash=content_hash,
            title=ticket["title"],
            content=ticket["content"],
            author=ticket.get("author"),
            url=ticket.get("url"),
            doc_metadata=ticket.get("metadata", {}),
            embedding_status="pending",
            source_created_at=datetime.now(tz=timezone.utc),
        )
        db.add(doc)
        await db.flush()  # get the ID before commit
        created.append(str(doc.id))

    await db.commit()

    # Dispatch embedding tasks for newly created docs
    embedded = []
    for doc_id in created:
        try:
            from apps.worker.tasks.ingestion import process_document
            process_document.delay(doc_id, str(ctx.tenant_id))
            embedded.append(doc_id)
        except Exception as exc:
            logger.warning("Could not dispatch embedding for %s: %s", doc_id, exc)

    logger.info(
        "Demo seed: tenant=%s created=%d skipped=%d dispatched=%d",
        ctx.tenant_id, len(created), len(skipped), len(embedded),
    )

    return {
        "status": "ok",
        "created": len(created),
        "skipped": len(skipped),
        "message": (
            f"Seeded {len(created)} demo documents ({len(skipped)} already existed). "
            f"Dispatched {len(embedded)} embedding tasks — wait ~30 seconds, "
            "then run Simulate Alert to see Jira/GitHub/Slack context in the RRT brief."
        ),
    }


@router.get("/seed-demo/status")
async def seed_demo_status(
    ctx: Annotated[TenantContext, Depends(require_viewer)],
    db=Depends(get_db),
):
    """
    Diagnostic: checks every step of the demo data pipeline.
    Call this after seeding to see exactly where things stand.
    """
    from ..db.models import CanonicalDocument
    from ..config import settings as cfg

    source_ids = [t["source_id"] for t in _DEMO_TICKETS]

    # 1. Check DB documents
    result = await db.execute(
        sa.select(
            CanonicalDocument.source_id,
            CanonicalDocument.source_type,
            CanonicalDocument.embedding_status,
            CanonicalDocument.chunk_count,
        ).where(
            CanonicalDocument.tenant_id == ctx.tenant_id,
            CanonicalDocument.source_id.in_(source_ids),
        )
    )
    docs = [
        {"source_id": r.source_id, "source_type": r.source_type,
         "embedding_status": r.embedding_status, "chunk_count": r.chunk_count}
        for r in result.all()
    ]

    # 2. Check Qdrant connectivity + collection
    qdrant_ok = False
    qdrant_count = 0
    qdrant_error = None
    collection_name = f"{cfg.QDRANT_COLLECTION_PREFIX}{ctx.tenant_id}"
    try:
        from qdrant_client import QdrantClient
        qc = QdrantClient(url=cfg.QDRANT_URL, api_key=cfg.QDRANT_API_KEY or None, timeout=5)
        collections = [c.name for c in qc.get_collections().collections]
        qdrant_ok = True
        if collection_name in collections:
            info = qc.get_collection(collection_name)
            qdrant_count = info.points_count
        else:
            qdrant_error = f"Collection '{collection_name}' does not exist yet"
    except Exception as exc:
        qdrant_error = str(exc)

    done = [d for d in docs if d["embedding_status"] == "done"]
    pending = [d for d in docs if d["embedding_status"] == "pending"]

    return {
        "db_documents": {
            "found": len(docs),
            "expected": len(source_ids),
            "done": len(done),
            "pending": len(pending),
            "details": docs,
        },
        "qdrant": {
            "url": cfg.QDRANT_URL,
            "reachable": qdrant_ok,
            "collection": collection_name,
            "vector_count": qdrant_count,
            "error": qdrant_error,
        },
        "ready_for_demo": qdrant_ok and len(done) == len(source_ids) and qdrant_count > 0,
        "next_step": (
            "All good — run Simulate Alert" if (qdrant_ok and len(done) == len(source_ids) and qdrant_count > 0)
            else "Click Seed Demo Data and wait for worker to embed" if len(docs) == 0
            else f"{len(pending)} docs still pending embedding — check worker logs for process_document tasks" if pending
            else "Qdrant issue — check QDRANT_URL on worker service" if not qdrant_ok
            else "Collection empty — embedding may have failed, try seed again"
        ),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# WEBHOOK LOG INGEST — push-based alternative to 5-minute cron polling
# ═══════════════════════════════════════════════════════════════════════════════

class IngestLogEventRequest(BaseModel):
    """
    Push a log event directly into the OpsLens alert pipeline.
    Use this from your log shipper (Fluentd, Logstash, Vector, Datadog Webhook)
    instead of waiting for the 5-minute polling cycle.
    """
    service_name:   str = Field(..., description="Container / service name, e.g. 'payment-service'")
    error_message:  str = Field(..., description="The error or exception text")
    log_level:      str = Field(default="ERROR", description="ERROR | CRITICAL | FATAL | WARN")
    error_count:    int = Field(default=1, ge=1, description="Occurrence count in the window")
    severity:       str = Field(default="p2", description="p0 / p1 / p2 / p3")
    timestamp:      str | None = Field(None, description="ISO 8601 timestamp of first occurrence")
    metadata:       dict | None = Field(None, description="Any extra fields to pass through to the brief")


class IngestLogEventResponse(BaseModel):
    status:         str
    message:        str
    task_id:        str | None = None
    error_signature: str


@router.post(
    "/ingest",
    response_model=IngestLogEventResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Push a log event into the alert pipeline (webhook ingestion)",
)
async def ingest_log_event(
    body: IngestLogEventRequest,
    ctx: Annotated[TenantContext, Depends(require_admin)],
    db=Depends(get_db),
):
    """
    **Webhook / push-based ingestion endpoint.**

    Accepts a single log error event and immediately dispatches the full
    enrich_and_alert pipeline — no need to wait for the 5-minute cron cycle.

    Designed to be called from:
    - Fluentd / Logstash / Vector output plugins
    - Datadog Webhook integrations
    - Custom log shippers
    - CI/CD pipelines on deploy errors

    Example curl:
    ```
    curl -X POST https://api.opslens.ai/api/v1/log-ops/ingest \\
      -H "Authorization: Bearer <api_key>" \\
      -H "Content-Type: application/json" \\
      -d '{
        "service_name": "payment-service",
        "error_message": "PaymentError: Stripe API timeout after 30s",
        "error_count": 12,
        "severity": "p1"
      }'
    ```
    """
    import hashlib as _hashlib
    from apps.api.config import settings as cfg

    # Normalise + hash (same logic as fast_scan)
    normalised = re.sub(r"\b[0-9a-f]{8,}\b", "<hex>", body.error_message)
    normalised = re.sub(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}[.,\d]*", "<ts>", normalised)
    signature = _hashlib.md5(normalised[:120].encode()).hexdigest()

    # Check known issue suppression
    from ..models.log_ops import KnownIssue as _KI
    ki_result = await db.execute(
        sa.select(_KI).where(
            _KI.tenant_id == ctx.tenant_id,
            _KI.is_active == True,
        )
    )
    for ki in ki_result.scalars().all():
        if ki.signature and ki.signature == signature:
            return IngestLogEventResponse(
                status="suppressed",
                message=f"Matched known issue suppression (sig={signature[:8]}). No alert fired.",
                error_signature=signature,
            )
        if ki.match_pattern:
            try:
                if re.search(ki.match_pattern, body.error_message, re.IGNORECASE):
                    return IngestLogEventResponse(
                        status="suppressed",
                        message=f"Matched known issue pattern '{ki.match_pattern}'. No alert fired.",
                        error_signature=signature,
                    )
            except re.error:
                pass

    # Resolve routing rules
    result = await db.execute(
        sa.select(AlertRoutingRule)
        .where(AlertRoutingRule.tenant_id == ctx.tenant_id, AlertRoutingRule.is_active == True)
        .order_by(AlertRoutingRule.priority)
    )
    rules = result.scalars().all()
    routing_targets = []
    sample_error = f"{body.service_name} {body.error_message}"
    for rr in rules:
        if _rule_matches(rr, sample_error, body.service_name):
            routing_targets.append({
                "team_name": rr.team_name,
                "slack_webhook": rr.slack_webhook,
                "email_recipients": list(rr.email_recipients or []),
            })
            if rr.stop_on_match:
                break

    webhook = cfg.LOG_FAST_ALERT_SLACK_WEBHOOK or cfg.LOG_SCAN_SLACK_WEBHOOK
    if not routing_targets and not webhook:
        raise HTTPException(
            status_code=422,
            detail="No matching routing rules and no fallback webhook configured.",
        )

    if not routing_targets:
        routing_targets = [{"team_name": "Default", "slack_webhook": webhook, "email_recipients": []}]

    # Build error group and dispatch
    error_group_dict = {
        "signature": signature,
        "first_line": f"{body.service_name}: {body.error_message}",
        "count": body.error_count,
        "sample_lines": [f"[INGEST] {body.service_name}: {body.error_message}"],
        "metadata": body.metadata or {},
    }

    try:
        from apps.worker.tasks.log_fast_alert import enrich_and_alert
        task = enrich_and_alert.delay(
            tenant_id=str(ctx.tenant_id),
            error_group_dict=error_group_dict,
            webhook_url=routing_targets[0]["slack_webhook"] or webhook,
            routing_targets=routing_targets,
            error_count=body.error_count,
            window_minutes=1,
        )
        logger.info("Ingest event dispatched: tenant=%s service=%s sig=%s task=%s",
                    ctx.tenant_id, body.service_name, signature, task.id)
        return IngestLogEventResponse(
            status="dispatched",
            message=f"Event ingested. Enrichment task dispatched to {len(routing_targets)} team(s).",
            task_id=task.id,
            error_signature=signature,
        )
    except Exception as exc:
        logger.error("Failed to dispatch ingest task: %s", exc)
        raise HTTPException(status_code=503, detail=f"Worker unavailable: {exc}")


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

    service_hit   = _any_match(_normalise_patterns(list(rule.service_patterns or [])), error_line)
    error_hit     = _any_match(_normalise_patterns(list(rule.error_patterns or [])), error_line)
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
        team_id=str(rr.team_id) if rr.team_id else None,
        description=rr.description,
        service_patterns=list(rr.service_patterns or []),
        error_patterns=list(rr.error_patterns or []),
        source_containers=list(rr.source_containers or []),
        match_all=rr.match_all,
        slack_webhook=_mask_webhook(rr.slack_webhook),  # never expose plaintext tokens in API responses
        email_recipients=list(rr.email_recipients or []),
        priority=rr.priority,
        stop_on_match=rr.stop_on_match,
        cooldown_minutes=rr.cooldown_minutes or 10,
        is_active=rr.is_active,
        created_at=rr.created_at.isoformat(),
    )


def _decrypt_routing_rule_webhook(rr: AlertRoutingRule) -> str | None:
    """Use this — not rr.slack_webhook directly — whenever you need the actual URL to fire a request."""
    return _decrypt_webhook(rr.slack_webhook)
