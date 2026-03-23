"""
OpsLens AI — Delivery Risk Timeline Router
===========================================
Unified timeline of changes and anomalies. Answers "what changed before the
spike?" by correlating GitHub activity, Jira transitions, deploy events, and
log anomalies on a single chronological feed.

Endpoints:
    GET    /api/v1/timeline                    — Query events (time window, type filter)
    GET    /api/v1/timeline/around/{brief_id}  — Events ±N min around an RRT Brief
    POST   /api/v1/timeline/events             — Manually record an event
    DELETE /api/v1/timeline/events/{id}        — Delete a manual event (admin)

Webhook receivers (no JWT — use a shared secret header instead):
    POST   /api/v1/timeline/webhooks/github    — GitHub webhook receiver
    POST   /api/v1/timeline/webhooks/jira      — Jira webhook receiver

Webhook authentication:
    GitHub: validates X-Hub-Signature-256 using GITHUB_WEBHOOK_SECRET
    Jira:   validates X-Jira-Webhook-Token against JIRA_WEBHOOK_TOKEN config value
"""
from __future__ import annotations

import hashlib
import hmac
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

import sqlalchemy as sa
from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from ..auth.dependencies import TenantContext, require_admin, require_viewer
from ..config import settings
from ..db.session import get_db
from ..models.timeline import TimelineEvent
from ..utils.logging import get_logger

router = APIRouter()
logger = get_logger(__name__)

# ── Valid source types ─────────────────────────────────────────────────────────
VALID_SOURCE_TYPES = {
    "github_pr", "github_commit", "github_deploy",
    "jira_issue", "log_anomaly", "rrt_brief", "manual",
}

VALID_SEVERITIES = {"critical", "high", "medium", "low", "info"}


# ── Schemas ───────────────────────────────────────────────────────────────────
class TimelineEventOut(BaseModel):
    id:            str
    source_type:   str
    occurred_at:   str
    title:         str
    description:   str | None
    service:       str | None
    team:          str | None
    actor:         str | None
    is_anomaly:    bool
    severity:      str | None
    external_url:  str | None
    metadata:      dict
    source_id:     str | None
    created_at:    str


class ManualEventRequest(BaseModel):
    occurred_at:  str = Field(..., description="ISO-8601 datetime when the event occurred")
    title:        str = Field(..., min_length=3, max_length=200)
    description:  str | None = None
    service:      str | None = None
    team:         str | None = None
    is_anomaly:   bool = False
    severity:     str | None = Field(None, description="critical|high|medium|low|info")
    external_url: str | None = None
    metadata:     dict = Field(default_factory=dict)


class TimelineWindow(BaseModel):
    brief_id:       str
    window_minutes: int = Field(default=60, ge=5, le=720)
    source_types:   list[str] = Field(default_factory=list)


# ── Query timeline ─────────────────────────────────────────────────────────────
@router.get("", response_model=list[TimelineEventOut])
async def list_timeline_events(
    ctx: Annotated[TenantContext, Depends(require_viewer)],
    db=Depends(get_db),
    from_time: str | None = Query(
        default=None,
        description="ISO-8601 start of window (default: 24h ago)"
    ),
    to_time: str | None = Query(
        default=None,
        description="ISO-8601 end of window (default: now)"
    ),
    source_type: str | None = Query(
        default=None,
        description=f"Filter by source type: {', '.join(sorted(VALID_SOURCE_TYPES))}"
    ),
    service: str | None = Query(default=None, description="Filter by service/repo name"),
    team: str | None = Query(default=None, description="Filter by team name"),
    anomalies_only: bool = Query(default=False, description="Only return anomaly events"),
    limit: int = Query(default=50, ge=1, le=500),
):
    """
    Fetch timeline events for this tenant within a time window.
    Returns newest-first by default.
    """
    now = datetime.now(tz=timezone.utc)
    start = _parse_dt(from_time) if from_time else now - timedelta(hours=24)
    end   = _parse_dt(to_time)   if to_time   else now

    q = (
        sa.select(TimelineEvent)
        .where(
            TimelineEvent.tenant_id  == ctx.tenant_id,
            TimelineEvent.occurred_at >= start,
            TimelineEvent.occurred_at <= end,
        )
        .order_by(TimelineEvent.occurred_at.desc())
        .limit(limit)
    )
    if source_type and source_type in VALID_SOURCE_TYPES:
        q = q.where(TimelineEvent.source_type == source_type)
    if service:
        q = q.where(TimelineEvent.service.ilike(f"%{service}%"))
    if team:
        q = q.where(TimelineEvent.team.ilike(f"%{team}%"))
    if anomalies_only:
        q = q.where(TimelineEvent.is_anomaly == True)

    result = await db.execute(q)
    return [_to_out(e) for e in result.scalars().all()]


# ── Correlation window — events around a brief ─────────────────────────────────
@router.get("/around/{brief_id}", response_model=list[TimelineEventOut])
async def timeline_around_brief(
    brief_id: str,
    ctx: Annotated[TenantContext, Depends(require_viewer)],
    db=Depends(get_db),
    window_minutes: int = Query(
        default=60, ge=5, le=720,
        description="Fetch events this many minutes before AND after the brief's detected_at"
    ),
    source_types: str | None = Query(
        default=None,
        description="Comma-separated source_type filter, e.g. github_pr,github_deploy,jira_issue"
    ),
):
    """
    Return all timeline events within ±window_minutes of when an RRT Brief was detected.
    This is the primary "what changed before the spike?" view.

    Events are returned oldest-first so the UI can render a proper timeline.
    The RRT Brief's own log_anomaly event is included if it was recorded.
    """
    from ..models.rrt_brief import RRTBrief

    result = await db.execute(
        sa.select(RRTBrief).where(
            RRTBrief.id == brief_id,
            RRTBrief.tenant_id == ctx.tenant_id,
        )
    )
    brief = result.scalar_one_or_none()
    if not brief:
        raise HTTPException(status_code=404, detail="RRT brief not found")

    anchor = brief.detected_at
    start  = anchor - timedelta(minutes=window_minutes)
    end    = anchor + timedelta(minutes=window_minutes)

    q = (
        sa.select(TimelineEvent)
        .where(
            TimelineEvent.tenant_id  == ctx.tenant_id,
            TimelineEvent.occurred_at >= start,
            TimelineEvent.occurred_at <= end,
        )
        .order_by(TimelineEvent.occurred_at.asc())
    )

    if source_types:
        type_list = [t.strip() for t in source_types.split(",") if t.strip() in VALID_SOURCE_TYPES]
        if type_list:
            q = q.where(TimelineEvent.source_type.in_(type_list))

    rows = await db.execute(q)
    events = [_to_out(e) for e in rows.scalars().all()]

    logger.info(
        "Timeline around brief %s: %d events in ±%dmin window",
        brief_id[:8], len(events), window_minutes,
    )
    return events


# ── Manual event ───────────────────────────────────────────────────────────────
@router.post("/events", response_model=TimelineEventOut, status_code=status.HTTP_201_CREATED)
async def create_manual_event(
    body: ManualEventRequest,
    ctx: Annotated[TenantContext, Depends(require_admin)],
    db=Depends(get_db),
):
    """
    Manually record a timeline event — planned maintenance, config change,
    feature flag flip, infrastructure change, etc.
    """
    if body.severity and body.severity not in VALID_SEVERITIES:
        raise HTTPException(status_code=400, detail=f"Invalid severity. Use: {VALID_SEVERITIES}")

    try:
        occurred_at = _parse_dt(body.occurred_at)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid occurred_at: {exc}") from exc

    event = TimelineEvent(
        id           = str(uuid.uuid4()),
        tenant_id    = ctx.tenant_id,
        source_type  = "manual",
        occurred_at  = occurred_at,
        title        = body.title,
        description  = body.description,
        service      = body.service,
        team         = body.team,
        actor        = ctx.user_id,
        is_anomaly   = body.is_anomaly,
        severity     = body.severity,
        external_url = body.external_url,
        event_metadata = body.metadata,
    )
    db.add(event)
    await db.commit()
    await db.refresh(event)
    logger.info("Manual timeline event created by %s: %s", ctx.user_id, body.title)
    return _to_out(event)


@router.delete("/events/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_timeline_event(
    event_id: str,
    ctx: Annotated[TenantContext, Depends(require_admin)],
    db=Depends(get_db),
):
    """Delete a manual timeline event (admin only). Cannot delete webhook-ingested events."""
    result = await db.execute(
        sa.select(TimelineEvent).where(
            TimelineEvent.id == event_id,
            TimelineEvent.tenant_id == ctx.tenant_id,
        )
    )
    event = result.scalar_one_or_none()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    if event.source_type != "manual":
        raise HTTPException(
            status_code=403,
            detail="Only manually-created events can be deleted. Webhook events are immutable."
        )
    await db.delete(event)
    await db.commit()


# ── GitHub Webhook ─────────────────────────────────────────────────────────────
@router.post("/webhooks/github", status_code=status.HTTP_202_ACCEPTED, include_in_schema=False)
async def github_webhook(
    request: Request,
    db=Depends(get_db),
    x_hub_signature_256: str | None = Header(default=None, alias="X-Hub-Signature-256"),
    x_github_event:      str | None = Header(default=None, alias="X-GitHub-Event"),
    x_github_delivery:   str | None = Header(default=None, alias="X-GitHub-Delivery"),
):
    """
    Receives GitHub webhook payloads and writes timeline events.

    Supported events:
        pull_request   — opened, closed (+ merged), synchronize
        push           — branch push (non-tag)
        workflow_run   — completed (deployment workflows)
        deployment_status — created (deploy started/succeeded/failed)

    Configure GitHub webhook:
        Payload URL:  https://<your-domain>/api/v1/timeline/webhooks/github
        Content type: application/json
        Secret:       <GITHUB_WEBHOOK_SECRET from config>
        Events:       Pull requests, Pushes, Workflow runs
    """
    body_bytes = await request.body()

    # ── Signature validation ──────────────────────────────────────────────────
    secret = getattr(settings, "GITHUB_WEBHOOK_SECRET", "")
    if secret:
        if not x_hub_signature_256:
            raise HTTPException(status_code=401, detail="Missing X-Hub-Signature-256")
        expected = "sha256=" + hmac.new(
            secret.encode(), body_bytes, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, x_hub_signature_256):
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

    import json
    try:
        payload: dict = json.loads(body_bytes)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    event_type = x_github_event or ""
    delivery_id = x_github_delivery or str(uuid.uuid4())

    # Tenant resolution: derive tenant from repo owner or use a default
    # In production this would map GitHub org → OpsLens tenant via an integrations table.
    # For now we use a deterministic sentinel that the ingestion flow can override.
    tenant_id = _resolve_github_tenant(payload)
    if not tenant_id:
        logger.debug("GitHub webhook ignored — could not resolve tenant for repo %s",
                     payload.get("repository", {}).get("full_name"))
        return {"status": "ignored", "reason": "tenant_not_found"}

    event = _parse_github_event(event_type, payload, delivery_id, tenant_id)
    if not event:
        return {"status": "ignored", "event_type": event_type}

    # Upsert: if we already have this delivery, skip
    if event.source_id:
        existing = await db.execute(
            sa.select(TimelineEvent.id).where(
                TimelineEvent.tenant_id == tenant_id,
                TimelineEvent.source_id == event.source_id,
            ).limit(1)
        )
        if existing.scalar_one_or_none():
            return {"status": "duplicate", "source_id": event.source_id}

    db.add(event)
    await db.commit()
    logger.info("GitHub timeline event saved: %s — %s", event.source_type, event.title[:60])
    return {"status": "ok", "event_id": event.id, "type": event.source_type}


# ── Jira Webhook ───────────────────────────────────────────────────────────────
@router.post("/webhooks/jira", status_code=status.HTTP_202_ACCEPTED, include_in_schema=False)
async def jira_webhook(
    request: Request,
    db=Depends(get_db),
    x_jira_webhook_token: str | None = Header(default=None, alias="X-Jira-Webhook-Token"),
):
    """
    Receives Jira webhook payloads for issue status transitions.

    Supported events:
        jira:issue_updated   — status field changed
        jira:issue_created   — new issue

    Configure Jira webhook:
        URL:    https://<your-domain>/api/v1/timeline/webhooks/jira
        Events: Issue Updated, Issue Created
        Secret token: <JIRA_WEBHOOK_TOKEN from config>
    """
    # ── Token validation ──────────────────────────────────────────────────────
    expected_token = getattr(settings, "JIRA_WEBHOOK_TOKEN", "")
    if expected_token:
        if x_jira_webhook_token != expected_token:
            raise HTTPException(status_code=401, detail="Invalid webhook token")

    body_bytes = await request.body()
    import json
    try:
        payload: dict = json.loads(body_bytes)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    webhook_event = payload.get("webhookEvent", "")
    issue = payload.get("issue", {})
    fields = issue.get("fields", {})
    changelog = payload.get("changelog", {})

    # Only care about status changes
    if webhook_event == "jira:issue_updated":
        status_change = None
        for item in changelog.get("items", []):
            if item.get("field") == "status":
                status_change = item
                break
        if not status_change:
            return {"status": "ignored", "reason": "no status change"}

    tenant_id = _resolve_jira_tenant(payload)
    if not tenant_id:
        logger.debug("Jira webhook ignored — could not resolve tenant for project %s",
                     fields.get("project", {}).get("key"))
        return {"status": "ignored", "reason": "tenant_not_found"}

    issue_key  = issue.get("key", "")
    summary    = (fields.get("summary") or "")[:200]
    issue_url  = f"{payload.get('baseUrl', '')}/browse/{issue_key}"
    assignee_obj = fields.get("assignee") or {}
    assignee   = assignee_obj.get("displayName") or assignee_obj.get("emailAddress")
    priority   = (fields.get("priority") or {}).get("name", "Medium")
    issue_type = (fields.get("issuetype") or {}).get("name", "Task")

    from_status = ""
    to_status   = ""
    if webhook_event == "jira:issue_updated":
        from_status = status_change.get("fromString", "")
        to_status   = status_change.get("toString", "")
        title = f"{issue_key} → {to_status}: {summary}"
        source_id = f"jira:{issue_key}:{status_change.get('id', uuid.uuid4())}"
    else:
        to_status = (fields.get("status") or {}).get("name", "To Do")
        title = f"{issue_key} created ({to_status}): {summary}"
        source_id = f"jira:{issue_key}:created"

    project_key = issue_key.split("-")[0] if "-" in issue_key else issue_key

    # Dedup
    existing = await db.execute(
        sa.select(TimelineEvent.id).where(
            TimelineEvent.tenant_id == tenant_id,
            TimelineEvent.source_id == source_id,
        ).limit(1)
    )
    if existing.scalar_one_or_none():
        return {"status": "duplicate", "source_id": source_id}

    event = TimelineEvent(
        id             = str(uuid.uuid4()),
        tenant_id      = tenant_id,
        source_type    = "jira_issue",
        occurred_at    = datetime.now(tz=timezone.utc),
        title          = title,
        description    = f"Priority: {priority} | Type: {issue_type}",
        service        = project_key,
        team           = None,
        actor          = assignee,
        is_anomaly     = to_status.lower() in ("reopened", "blocked", "failed"),
        severity       = _jira_priority_to_severity(priority),
        external_url   = issue_url if issue_url.startswith("http") else None,
        event_metadata = {
            "issue_key":   issue_key,
            "issue_url":   issue_url,
            "summary":     summary,
            "from_status": from_status,
            "to_status":   to_status,
            "assignee":    assignee,
            "priority":    priority,
            "issue_type":  issue_type,
        },
        source_id = source_id,
    )
    db.add(event)
    await db.commit()
    logger.info("Jira timeline event saved: %s — %s", issue_key, to_status)
    return {"status": "ok", "event_id": event.id}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _parse_dt(s: str) -> datetime:
    """Parse an ISO-8601 string into an aware datetime (UTC if no tz specified)."""
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _to_out(e: TimelineEvent) -> TimelineEventOut:
    return TimelineEventOut(
        id           = str(e.id),
        source_type  = e.source_type,
        occurred_at  = e.occurred_at.isoformat(),
        title        = e.title,
        description  = e.description,
        service      = e.service,
        team         = e.team,
        actor        = e.actor,
        is_anomaly   = e.is_anomaly,
        severity     = e.severity,
        external_url = e.external_url,
        metadata     = dict(e.event_metadata or {}),
        source_id    = e.source_id,
        created_at   = e.created_at.isoformat(),
    )


def _resolve_github_tenant(payload: dict) -> str | None:
    """
    Map a GitHub org/owner to a tenant_id.

    Production implementation: look up the GitHub app installation in the
    integrations table (WHERE integration_type='github' AND config->>'org' = owner).

    For now, fall back to the default tenant from settings if the org matches
    the configured GITHUB_ORG, or accept any payload when GITHUB_ORG is unset.
    """
    repo = payload.get("repository") or {}
    owner = (repo.get("owner") or {}).get("login", "")
    configured_org = getattr(settings, "GITHUB_ORG", "") or ""
    if configured_org and owner.lower() != configured_org.lower():
        return None
    # Fall back to the environment-level default tenant for dev/single-tenant installs
    return getattr(settings, "DEFAULT_TENANT_ID", None) or "default"


def _resolve_jira_tenant(payload: dict) -> str | None:
    """Map a Jira project to a tenant_id (same pattern as GitHub resolver)."""
    configured_host = getattr(settings, "JIRA_HOST", "") or ""
    base_url = payload.get("baseUrl", "") or ""
    if configured_host and configured_host not in base_url:
        return None
    return getattr(settings, "DEFAULT_TENANT_ID", None) or "default"


def _parse_github_event(
    event_type: str,
    payload: dict,
    delivery_id: str,
    tenant_id: str,
) -> TimelineEvent | None:
    """Parse a raw GitHub webhook payload into a TimelineEvent (or None to skip)."""
    repo    = payload.get("repository") or {}
    repo_name = repo.get("name", "unknown")
    repo_url  = repo.get("html_url", "")
    sender    = (payload.get("sender") or {}).get("login", "")

    if event_type == "pull_request":
        pr = payload.get("pull_request") or {}
        action = payload.get("action", "")
        if action not in ("opened", "closed", "synchronize", "reopened"):
            return None

        number   = pr.get("number", 0)
        title    = pr.get("title", "")
        pr_url   = pr.get("html_url", "")
        merged   = pr.get("merged", False)
        base_br  = (pr.get("base") or {}).get("ref", "main")
        head_br  = (pr.get("head") or {}).get("ref", "")
        author   = (pr.get("user") or {}).get("login", sender)
        merged_by_obj = pr.get("merged_by") or {}
        merged_by = merged_by_obj.get("login", "")
        additions = pr.get("additions", 0)
        deletions = pr.get("deletions", 0)
        changed_files = pr.get("changed_files", 0)

        if action == "closed" and merged:
            event_title = f"PR #{number} merged → {base_br}: {title}"
            is_anomaly = False
        elif action == "closed":
            event_title = f"PR #{number} closed (unmerged): {title}"
            is_anomaly = False
        elif action == "opened":
            event_title = f"PR #{number} opened: {title}"
            is_anomaly = False
        else:
            event_title = f"PR #{number} updated: {title}"
            is_anomaly = False

        # Large PRs to main/master are higher-risk
        severity = "info"
        if merged and base_br in ("main", "master", "production"):
            if changed_files > 20 or additions + deletions > 500:
                severity = "medium"
            else:
                severity = "low"

        source_id = f"gh:pr:{repo.get('full_name','')}:{number}:{action}"

        return TimelineEvent(
            id             = str(uuid.uuid4()),
            tenant_id      = tenant_id,
            source_type    = "github_pr",
            occurred_at    = _gh_dt(pr.get("updated_at") or pr.get("created_at")),
            title          = event_title,
            description    = f"+{additions} −{deletions} across {changed_files} files | base: {base_br}",
            service        = repo_name,
            team           = None,
            actor          = merged_by or author,
            is_anomaly     = is_anomaly,
            severity       = severity,
            external_url   = pr_url,
            event_metadata = {
                "pr_number":     number,
                "pr_url":        pr_url,
                "author":        author,
                "branch":        head_br,
                "base_branch":   base_br,
                "additions":     additions,
                "deletions":     deletions,
                "files_changed": changed_files,
                "merged":        merged,
                "merged_by":     merged_by,
            },
            source_id = source_id,
        )

    elif event_type == "push":
        # Ignore tag pushes and branch deletes
        ref     = payload.get("ref", "")
        deleted = payload.get("deleted", False)
        if not ref.startswith("refs/heads/") or deleted:
            return None

        branch   = ref.replace("refs/heads/", "")
        commits  = payload.get("commits") or []
        count    = len(commits)
        if count == 0:
            return None

        head_commit = payload.get("head_commit") or commits[-1] if commits else {}
        sha_short   = (head_commit.get("id") or "")[:7]
        message     = (head_commit.get("message") or "").split("\n")[0][:120]
        author      = (head_commit.get("author") or {}).get("name", sender)

        return TimelineEvent(
            id             = str(uuid.uuid4()),
            tenant_id      = tenant_id,
            source_type    = "github_commit",
            occurred_at    = _gh_dt(head_commit.get("timestamp")),
            title          = f"Push to {branch} ({count} commit{'s' if count != 1 else ''}): {message}",
            description    = f"Repo: {repo_name} | SHA: {sha_short}",
            service        = repo_name,
            team           = None,
            actor          = author,
            is_anomaly     = False,
            severity       = "low" if branch in ("main", "master", "production") else "info",
            external_url   = repo_url + f"/commit/{head_commit.get('id', '')}",
            event_metadata = {
                "sha":           head_commit.get("id", ""),
                "short_sha":     sha_short,
                "message":       message,
                "author":        author,
                "branch":        branch,
                "url":           head_commit.get("url", ""),
                "commits_count": count,
            },
            source_id = f"gh:push:{delivery_id}",
        )

    elif event_type == "workflow_run":
        run    = payload.get("workflow_run") or {}
        action = payload.get("action", "")
        if action not in ("completed", "requested"):
            return None

        wf_name    = run.get("name", "workflow")
        conclusion = run.get("conclusion") or "in_progress"
        run_url    = run.get("html_url", "")
        run_id     = run.get("id", 0)
        branch     = run.get("head_branch", "")
        actor_login = (run.get("actor") or {}).get("login", sender)

        # Only care about deployment-like workflows
        deploy_keywords = ("deploy", "release", "publish", "prod", "staging", "production")
        if not any(kw in wf_name.lower() for kw in deploy_keywords):
            return None

        is_failure   = conclusion in ("failure", "timed_out", "cancelled")
        is_anomaly   = is_failure
        severity_map = {
            "failure": "high", "timed_out": "medium",
            "cancelled": "low", "success": "info", "in_progress": "info",
        }
        run_status   = conclusion if action == "completed" else "started"

        title = f"Deploy: {wf_name} on {branch} ({run_status.upper()})"

        return TimelineEvent(
            id             = str(uuid.uuid4()),
            tenant_id      = tenant_id,
            source_type    = "github_deploy",
            occurred_at    = _gh_dt(run.get("updated_at") or run.get("created_at")),
            title          = title,
            description    = f"Repo: {repo_name} | Conclusion: {conclusion}",
            service        = repo_name,
            team           = None,
            actor          = actor_login,
            is_anomaly     = is_anomaly,
            severity       = severity_map.get(conclusion, "info"),
            external_url   = run_url,
            event_metadata = {
                "workflow_name": wf_name,
                "run_id":        run_id,
                "run_url":       run_url,
                "environment":   branch,
                "triggered_by":  actor_login,
                "status":        run_status,
                "conclusion":    conclusion,
            },
            source_id = f"gh:workflow_run:{run_id}:{action}",
        )

    return None


def _gh_dt(s: str | None) -> datetime:
    """Parse a GitHub timestamp string, defaulting to now."""
    if not s:
        return datetime.now(tz=timezone.utc)
    try:
        return _parse_dt(s)
    except Exception:
        return datetime.now(tz=timezone.utc)


def _jira_priority_to_severity(priority: str) -> str:
    mapping = {
        "blocker": "critical", "critical": "critical",
        "highest": "critical", "high": "high",
        "medium": "medium", "low": "low",
        "lowest": "info", "trivial": "info",
    }
    return mapping.get(priority.lower(), "medium")
