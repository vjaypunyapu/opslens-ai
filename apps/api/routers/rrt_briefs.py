"""
OpsLens AI — RRT Briefs Router
================================
Manages Rapid Response Team incident briefs.

Endpoints:
    GET    /api/v1/rrt-briefs                — List briefs (paginated, filterable)
    GET    /api/v1/rrt-briefs/{id}           — Get a single brief
    PATCH  /api/v1/rrt-briefs/{id}           — Update status / resolution notes
    POST   /api/v1/rrt-briefs/{id}/update    — Post a status update (notifies Slack)
    DELETE /api/v1/rrt-briefs/{id}           — Delete a brief (admin only)
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated

import httpx
import sqlalchemy as sa
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from ..auth.dependencies import TenantContext, require_admin, require_viewer
from ..db.session import get_db
from ..models.rrt_brief import RRTBrief
from ..utils.logging import get_logger

router = APIRouter()
logger = get_logger(__name__)

VALID_STATUSES = {"open", "investigating", "resolved"}


# ── Schemas ───────────────────────────────────────────────────────────────────
class RRTBriefOut(BaseModel):
    id:               str
    title:            str
    what_happened:    str
    impact:           str | None
    started_at:       str | None
    detected_at:      str
    suspected_cause:  str | None
    next_actions:     list[str]
    related_items:    list[dict]
    owner_team:       str | None
    owner_contacts:   list[str]
    status:           str
    resolved_at:      str | None
    resolution_notes: str | None
    error_signature:  str | None
    error_sample:     str | None
    channels_sent:    list[str]
    jira_ticket_key:  str | None
    jira_ticket_url:  str | None
    created_at:       str
    updated_at:       str


class UpdateBriefRequest(BaseModel):
    status:           str | None = Field(
        None,
        description="New status: open | investigating | resolved"
    )
    resolution_notes: str | None = None
    impact:           str | None = None
    owner_team:       str | None = None


class PostUpdateRequest(BaseModel):
    update_text: str = Field(
        ...,
        min_length=1,
        max_length=1000,
        description="Free-text status update to post in Slack thread and record"
    )
    status: str | None = Field(
        None,
        description="Optionally change the status at the same time"
    )


# ── Manual generate (demo/testing) ───────────────────────────────────────────

class GenerateBriefRequest(BaseModel):
    service_name: str = Field(..., min_length=1, max_length=100)
    error_message: str = Field(..., min_length=5, max_length=1000)
    error_count: int = Field(default=12, ge=1, le=999)
    webhook_url: str | None = Field(
        None,
        description="Slack webhook override. Falls back to env LOG_FAST_ALERT_SLACK_WEBHOOK.",
    )


@router.post(
    "/generate",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Manually trigger RRT brief generation (demo / testing)",
)
async def generate_rrt_brief_manual(
    body: GenerateBriefRequest,
    ctx: Annotated[TenantContext, Depends(require_admin)],
):
    """
    Manually dispatch an RRT brief generation task. Useful for demos and testing
    when you want to trigger *only* the brief (without the full alert pipeline).

    For the full end-to-end demo (fast alert → enrichment → brief), use
    `POST /api/v1/log-ops/simulate` instead.
    """
    import hashlib as _hashlib
    import re as _re
    from apps.api.config import settings as cfg

    webhook = body.webhook_url or cfg.LOG_FAST_ALERT_SLACK_WEBHOOK or cfg.LOG_SCAN_SLACK_WEBHOOK
    if not webhook:
        raise HTTPException(
            status_code=422,
            detail="No Slack webhook configured. Provide webhook_url or set LOG_FAST_ALERT_SLACK_WEBHOOK.",
        )

    normalised = _re.sub(r"\b[0-9a-f]{8,}\b", "<hex>", body.error_message)
    normalised = _re.sub(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}[.,\d]*", "<ts>", normalised)
    signature = _hashlib.md5(normalised[:120].encode()).hexdigest()

    error_group_dict = {
        "signature": signature,
        "first_line": f"[DEMO] {body.service_name}: {body.error_message}",
        "count": body.error_count,
        "sample_lines": [
            f"[SIMULATED] ERROR {body.service_name}: {body.error_message}",
            f"    Traceback (most recent call last):",
            f"      File \"{body.service_name}/main.py\", line 42, in handle_request",
            f"    {body.error_message}",
            f"    [Count in window: {body.error_count} occurrences]",
        ],
    }

    routing_targets = [{"team_name": "Demo Team", "slack_webhook": webhook, "email_recipients": []}]

    try:
        from apps.worker.tasks.rrt_briefing import generate_rrt_brief
        task = generate_rrt_brief.delay(
            tenant_id=str(ctx.tenant_id),
            error_group_dict=error_group_dict,
            related_items=[],
            routing_targets=routing_targets,
            error_count=body.error_count,
            window_minutes=5,
        )
        return {
            "status": "dispatched",
            "task_id": task.id,
            "message": (
                "RRT brief generation started. Check your Slack channel in ~20–30 seconds "
                "and visit /api/v1/rrt-briefs to see the result."
            ),
            "error_signature": signature,
        }
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Could not dispatch task — is the Celery worker running? ({exc})",
        )


# ── List briefs ───────────────────────────────────────────────────────────────
@router.get("", response_model=list[RRTBriefOut])
async def list_rrt_briefs(
    ctx: Annotated[TenantContext, Depends(require_viewer)],
    db=Depends(get_db),
    status_filter: str | None = Query(
        default=None,
        alias="status",
        description="Filter by status: open | investigating | resolved"
    ),
    days: int = Query(default=7, ge=1, le=90),
    limit: int = Query(default=20, ge=1, le=100),
):
    """
    List recent RRT briefs for this tenant.
    Filter by status and time window. Returns newest first.
    """
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
    q = (
        sa.select(RRTBrief)
        .where(
            RRTBrief.tenant_id == ctx.tenant_id,
            RRTBrief.detected_at >= cutoff,
        )
        .order_by(RRTBrief.detected_at.desc())
        .limit(limit)
    )
    if status_filter and status_filter in VALID_STATUSES:
        q = q.where(RRTBrief.status == status_filter)

    result = await db.execute(q)
    return [_to_out(r) for r in result.scalars().all()]


# ── Get single brief ──────────────────────────────────────────────────────────
@router.get("/{brief_id}", response_model=RRTBriefOut)
async def get_rrt_brief(
    brief_id: str,
    ctx: Annotated[TenantContext, Depends(require_viewer)],
    db=Depends(get_db),
):
    return _to_out(await _get_or_404(db, brief_id, ctx.tenant_id))


# ── Update status / fields ────────────────────────────────────────────────────
@router.patch("/{brief_id}", response_model=RRTBriefOut)
async def update_rrt_brief(
    brief_id: str,
    body: UpdateBriefRequest,
    ctx: Annotated[TenantContext, Depends(require_admin)],
    db=Depends(get_db),
):
    """
    Update the status, resolution notes, impact, or owner of a brief.
    If status is changed to 'resolved', resolved_at is set automatically.
    """
    brief = await _get_or_404(db, brief_id, ctx.tenant_id)

    if body.status is not None:
        if body.status not in VALID_STATUSES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status. Must be one of: {VALID_STATUSES}"
            )
        brief.status = body.status
        if body.status == "resolved" and not brief.resolved_at:
            brief.resolved_at = datetime.now(tz=timezone.utc)

    if body.resolution_notes is not None:
        brief.resolution_notes = body.resolution_notes
    if body.impact is not None:
        brief.impact = body.impact
    if body.owner_team is not None:
        brief.owner_team = body.owner_team

    await db.commit()
    await db.refresh(brief)
    logger.info("RRT brief %s updated by %s — status=%s", brief_id[:8], ctx.user_id, brief.status)
    return _to_out(brief)


# ── Post status update (notifies Slack) ──────────────────────────────────────
@router.post("/{brief_id}/update", status_code=status.HTTP_202_ACCEPTED)
async def post_rrt_update(
    brief_id: str,
    body: PostUpdateRequest,
    ctx: Annotated[TenantContext, Depends(require_admin)],
    db=Depends(get_db),
):
    """
    Post a status update for a brief. Notifies Slack (via webhook) and
    optionally changes the status. Useful during incident response to keep
    the team informed without leaving Slack.

    Example:
        { "update_text": "Root cause confirmed: PR #87 removed tenant_id. Rollback in progress.", "status": "investigating" }
    """
    brief = await _get_or_404(db, brief_id, ctx.tenant_id)

    # Optionally update status
    if body.status and body.status in VALID_STATUSES:
        brief.status = body.status
        if body.status == "resolved" and not brief.resolved_at:
            brief.resolved_at = datetime.now(tz=timezone.utc)
        await db.commit()

    # Find the Slack webhook for this brief's team
    webhook_url = await _get_team_webhook(db, ctx.tenant_id, brief.owner_team)

    if webhook_url:
        try:
            from apps.worker.tasks.rrt_briefing import send_rrt_status_update
            send_rrt_status_update.delay(
                brief_id=brief_id,
                title=brief.title,
                status=brief.status,
                update_text=body.update_text,
                webhook_url=webhook_url,
                updated_by=ctx.user_id,
            )
        except Exception as exc:
            logger.warning("Could not dispatch Slack update for brief %s: %s", brief_id[:8], exc)

    logger.info(
        "Update posted for brief %s by %s: '%s'",
        brief_id[:8], ctx.user_id, body.update_text[:80],
    )
    return {
        "message": "Update posted",
        "brief_id": brief_id,
        "new_status": brief.status,
        "slack_notified": webhook_url is not None,
    }


# ── Push to Jira ──────────────────────────────────────────────────────────────
class PushToJiraResponse(BaseModel):
    ticket_key: str
    ticket_url: str
    already_existed: bool


async def _sync_jira_after_push(integration_id: str, tenant_id: str) -> None:
    """Background task: re-sync Jira so the new ticket lands in Qdrant immediately."""
    try:
        from ..services.direct_sync_service import run_direct_sync
        await run_direct_sync(integration_id, tenant_id)
        logger.info("Background Jira sync completed after push-to-jira (tenant=%s)", tenant_id)
    except Exception as exc:
        logger.warning("Background Jira sync failed (non-fatal): %s", exc)


@router.post("/{brief_id}/push-to-jira", response_model=PushToJiraResponse)
async def push_to_jira(
    brief_id: str,
    background_tasks: BackgroundTasks,
    ctx: Annotated[TenantContext, Depends(require_admin)],
    db=Depends(get_db),
):
    """
    Create a Jira issue from this RRT brief using the tenant's connected
    Jira integration credentials.  If a ticket was already created for this
    brief, returns the existing key without creating a duplicate.

    Requires: a Jira integration connected via Integrations → Jira.
    """
    # ── 1. Load brief ──────────────────────────────────────────────────────────
    result = await db.execute(
        sa.select(RRTBrief).where(
            RRTBrief.id == brief_id,
            RRTBrief.tenant_id == str(ctx.tenant_uuid),
        )
    )
    brief: RRTBrief | None = result.scalar_one_or_none()
    if not brief:
        raise HTTPException(status_code=404, detail="Brief not found")

    # ── 2. Already pushed? Return existing ticket ──────────────────────────────
    if brief.jira_ticket_key and brief.jira_ticket_url:
        return PushToJiraResponse(
            ticket_key=brief.jira_ticket_key,
            ticket_url=brief.jira_ticket_url,
            already_existed=True,
        )

    # ── 3. Load Jira credentials from Integration table ────────────────────────
    from ..db.models import Integration
    from ..utils.crypto import decrypt_credentials

    intg_result = await db.execute(
        sa.select(Integration).where(
            Integration.tenant_id == ctx.tenant_uuid,
            Integration.source_type == "jira",
            Integration.status == "active",
        )
    )
    integration: Integration | None = intg_result.scalar_one_or_none()
    if not integration:
        raise HTTPException(
            status_code=422,
            detail="No active Jira integration found. Connect Jira first via Integrations → Jira.",
        )

    try:
        creds = decrypt_credentials(integration.credentials)
    except Exception:
        raise HTTPException(status_code=422, detail="Failed to decrypt Jira credentials")

    server_url = creds.get("server_url", "").rstrip("/")
    email      = creds.get("email", "")
    api_token  = creds.get("api_token", "")
    project_key = creds.get("project_key", "")  # optional — default to first available

    if not (server_url and email and api_token):
        raise HTTPException(status_code=422, detail="Jira credentials incomplete (need server_url, email, api_token)")

    # ── 4. Resolve project key if not stored in creds ──────────────────────────
    auth = (email, api_token)
    headers = {"Accept": "application/json", "Content-Type": "application/json"}

    async with httpx.AsyncClient(auth=auth, headers=headers, timeout=15) as client:
        if not project_key:
            proj_resp = await client.get(f"{server_url}/rest/api/3/project/search?maxResults=1")
            if proj_resp.is_success:
                projects = proj_resp.json().get("values", [])
                project_key = projects[0]["key"] if projects else ""
            if not project_key:
                raise HTTPException(
                    status_code=422,
                    detail="Could not determine Jira project key. Add 'project_key' to your Jira credentials.",
                )

        # ── 5. Build issue description ─────────────────────────────────────────
        sections = []
        if brief.what_happened:
            sections.append(f"*What happened*\n{brief.what_happened}")
        if brief.impact:
            sections.append(f"*Impact*\n{brief.impact}")
        if brief.suspected_cause:
            sections.append(f"*Suspected cause*\n{brief.suspected_cause}")
        if brief.next_actions:
            steps = "\n".join(f"• {a}" for a in brief.next_actions)
            sections.append(f"*Next actions*\n{steps}")
        if brief.error_sample:
            sections.append(f"*Error sample*\n{{code}}{brief.error_sample[:1000]}{{code}}")

        description_text = "\n\n".join(sections)

        # Jira Cloud uses Atlassian Document Format (ADF) for descriptions
        adf_description = {
            "version": 1,
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": description_text}],
                }
            ],
        }

        # ── 6. Create the Jira issue ───────────────────────────────────────────
        priority_map = {"p0": "Highest", "p1": "High", "p2": "Medium", "p3": "Low", "p4": "Lowest"}
        severity = getattr(brief, "severity", "p2") or "p2"

        issue_body = {
            "fields": {
                "project":     {"key": project_key},
                "summary":     f"[OpsLens] {brief.title}"[:255],
                "description": adf_description,
                "issuetype":   {"name": "Bug"},
                "priority":    {"name": priority_map.get(severity, "Medium")},
                "labels":      ["opslens", "incident"],
            }
        }

        create_resp = await client.post(f"{server_url}/rest/api/3/issue", json=issue_body)
        if not create_resp.is_success:
            logger.error("Jira issue creation failed: %s — %s", create_resp.status_code, create_resp.text[:300])
            raise HTTPException(
                status_code=502,
                detail=f"Jira returned {create_resp.status_code}: {create_resp.text[:200]}",
            )

        issue_data = create_resp.json()
        ticket_key = issue_data["key"]
        ticket_url = f"{server_url}/browse/{ticket_key}"

    # ── 7. Persist the ticket key on the brief ─────────────────────────────────
    await db.execute(
        sa.update(RRTBrief)
        .where(RRTBrief.id == brief_id)
        .values(jira_ticket_key=ticket_key, jira_ticket_url=ticket_url)
    )
    await db.commit()

    # ── 8. Re-sync Jira in background so new ticket lands in Qdrant immediately ──
    background_tasks.add_task(
        _sync_jira_after_push,
        str(integration.id),
        str(ctx.tenant_uuid),
    )

    logger.info("Jira ticket %s created for brief %s (tenant %s)", ticket_key, brief_id, ctx.tenant_uuid)
    return PushToJiraResponse(ticket_key=ticket_key, ticket_url=ticket_url, already_existed=False)


# ── Delete brief ──────────────────────────────────────────────────────────────
@router.delete("/{brief_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rrt_brief(
    brief_id: str,
    ctx: Annotated[TenantContext, Depends(require_admin)],
    db=Depends(get_db),
):
    brief = await _get_or_404(db, brief_id, ctx.tenant_id)
    await db.delete(brief)
    await db.commit()
    logger.info("RRT brief %s deleted by %s", brief_id[:8], ctx.user_id)


# ── Helpers ───────────────────────────────────────────────────────────────────
async def _get_or_404(db, brief_id: str, tenant_id: str) -> RRTBrief:
    result = await db.execute(
        sa.select(RRTBrief).where(
            RRTBrief.id == brief_id,
            RRTBrief.tenant_id == tenant_id,
        )
    )
    obj = result.scalar_one_or_none()
    if not obj:
        raise HTTPException(status_code=404, detail="RRT brief not found")
    return obj


async def _get_team_webhook(db, tenant_id: str, team_name: str | None) -> str | None:
    """Look up the Slack webhook for a team from AlertRoutingRule, falling back to config."""
    from apps.api.config import settings as cfg
    if team_name:
        try:
            from ..models.log_ops import AlertRoutingRule
            result = await db.execute(
                sa.select(AlertRoutingRule.slack_webhook).where(
                    AlertRoutingRule.tenant_id == tenant_id,
                    AlertRoutingRule.team_name == team_name,
                    AlertRoutingRule.is_active == True,
                ).limit(1)
            )
            webhook = result.scalar_one_or_none()
            if webhook:
                return webhook
        except Exception:
            pass
    return cfg.LOG_FAST_ALERT_SLACK_WEBHOOK or cfg.LOG_SCAN_SLACK_WEBHOOK


def _to_out(r: RRTBrief) -> RRTBriefOut:
    return RRTBriefOut(
        id=str(r.id),
        title=r.title,
        what_happened=r.what_happened,
        impact=r.impact,
        started_at=r.started_at.isoformat() if r.started_at else None,
        detected_at=r.detected_at.isoformat(),
        suspected_cause=r.suspected_cause,
        next_actions=list(r.next_actions or []),
        related_items=list(r.related_items or []),
        owner_team=r.owner_team,
        owner_contacts=list(r.owner_contacts or []),
        status=r.status,
        resolved_at=r.resolved_at.isoformat() if r.resolved_at else None,
        resolution_notes=r.resolution_notes,
        error_signature=r.error_signature,
        error_sample=r.error_sample,
        channels_sent=list(r.channels_sent or []),
        jira_ticket_key=r.jira_ticket_key,
        jira_ticket_url=r.jira_ticket_url,
        created_at=r.created_at.isoformat(),
        updated_at=r.updated_at.isoformat(),
    )
