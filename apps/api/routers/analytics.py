"""
OpsLens AI — Analytics Router
==============================
Endpoints:
    GET  /api/v1/analytics/error-trends     — Time-series error counts per service
                                              Used for anomaly / slow-degradation detection
    POST /api/v1/analytics/retrospective    — AI-generated sprint / monthly retrospective
                                              covering incidents, deploys, team patterns
                                              and recommendations
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..auth.dependencies import TenantContext, require_viewer, require_member
from ..config import settings
from ..db.models import CanonicalDocument, Incident
from ..db.session import get_db
from ..utils.logging import get_logger

router = APIRouter()
logger = get_logger(__name__)


# ── Schemas ────────────────────────────────────────────────────────────────────

class ErrorTrendPoint(BaseModel):
    date: str          # ISO date string YYYY-MM-DD
    service: str
    count: int


class AnomalyFlag(BaseModel):
    service: str
    trend: str         # "rising" | "stable" | "falling"
    change_pct: float  # % change from first half to second half of window
    avg_per_day: float


class ErrorTrendsResponse(BaseModel):
    days: int
    points: list[ErrorTrendPoint]
    anomalies: list[AnomalyFlag]   # services with notable upward trends


class RetrospectiveRequest(BaseModel):
    start_date: str = Field(..., description="ISO date string YYYY-MM-DD")
    end_date:   str = Field(..., description="ISO date string YYYY-MM-DD")


class RetrospectiveResponse(BaseModel):
    period:          str
    generated_at:    str
    incidents:       dict        # summary stats + top incidents
    deploys:         dict        # PRs merged, config changes
    team_patterns:   dict        # on-call load, recurring services, response times
    recommendations: list[str]  # AI-generated action items


# ── Error Trends ──────────────────────────────────────────────────────────────

@router.get("/error-trends", response_model=ErrorTrendsResponse)
async def get_error_trends(
    ctx: Annotated[TenantContext, Depends(require_viewer)],
    db=Depends(get_db),
    days: int = Query(default=30, ge=7, le=90, description="Lookback window in days"),
):
    """
    Returns daily incident counts grouped by service for the last N days.
    Also flags services whose error rate is trending upward (anomaly detection).
    """
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)

    result = await db.execute(
        sa.select(
            sa.func.date_trunc("day", Incident.started_at).label("day"),
            sa.func.coalesce(Incident.service, "unknown").label("service"),
            sa.func.count(Incident.id).label("cnt"),
        )
        .where(
            Incident.tenant_id == ctx.tenant_uuid,
            Incident.started_at >= cutoff,
        )
        .group_by("day", "service")
        .order_by("day")
    )
    rows = result.all()

    points = [
        ErrorTrendPoint(
            date=row.day.strftime("%Y-%m-%d"),
            service=row.service,
            count=row.cnt,
        )
        for row in rows
    ]

    # ── Anomaly detection: compare first-half vs second-half of the window ─────
    anomalies: list[AnomalyFlag] = []
    services = {p.service for p in points}
    midpoint = cutoff + timedelta(days=days // 2)

    for svc in services:
        svc_points = [p for p in points if p.service == svc]
        first_half  = [p.count for p in svc_points if p.date < midpoint.strftime("%Y-%m-%d")]
        second_half = [p.count for p in svc_points if p.date >= midpoint.strftime("%Y-%m-%d")]

        if not first_half or not second_half:
            continue

        avg_first  = sum(first_half)  / len(first_half)
        avg_second = sum(second_half) / len(second_half)
        avg_total  = sum(p.count for p in svc_points) / len(svc_points)

        if avg_first == 0:
            change_pct = 100.0 if avg_second > 0 else 0.0
        else:
            change_pct = ((avg_second - avg_first) / avg_first) * 100

        trend = (
            "rising"  if change_pct > 20  else
            "falling" if change_pct < -20 else
            "stable"
        )

        anomalies.append(AnomalyFlag(
            service=svc,
            trend=trend,
            change_pct=round(change_pct, 1),
            avg_per_day=round(avg_total, 2),
        ))

    # Sort: rising first, then by magnitude
    anomalies.sort(key=lambda a: (-abs(a.change_pct), a.trend != "rising"))

    return ErrorTrendsResponse(days=days, points=points, anomalies=anomalies)


# ── Retrospective ─────────────────────────────────────────────────────────────

@router.post("/retrospective", response_model=RetrospectiveResponse)
async def generate_retrospective(
    body: RetrospectiveRequest,
    ctx: Annotated[TenantContext, Depends(require_member)],
    db=Depends(get_db),
):
    """
    Generate an AI retrospective for the given date range covering:
    - Incidents & errors (what broke, severity, root causes)
    - Deploys & changes (PRs merged, Jira tickets closed)
    - Team patterns (most affected services, recurring issues, response times)
    - Recommendations (concrete AI-generated action items)
    """
    try:
        start_dt = datetime.fromisoformat(body.start_date).replace(tzinfo=timezone.utc)
        end_dt   = datetime.fromisoformat(body.end_date).replace(
            hour=23, minute=59, second=59, tzinfo=timezone.utc
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

    if end_dt <= start_dt:
        raise HTTPException(status_code=400, detail="end_date must be after start_date.")

    if (end_dt - start_dt).days > 120:
        raise HTTPException(status_code=400, detail="Date range cannot exceed 120 days.")

    tenant_uuid = ctx.tenant_uuid

    # ── 1. Fetch incidents ────────────────────────────────────────────────────
    inc_result = await db.execute(
        sa.select(Incident)
        .where(
            Incident.tenant_id == tenant_uuid,
            Incident.started_at >= start_dt,
            Incident.started_at <= end_dt,
        )
        .order_by(Incident.started_at)
    )
    incidents = inc_result.scalars().all()

    # ── 2. Fetch canonical documents (GitHub PRs, Jira, Slack) ───────────────
    doc_result = await db.execute(
        sa.select(
            CanonicalDocument.source_type,
            CanonicalDocument.title,
            CanonicalDocument.author,
            CanonicalDocument.url,
            CanonicalDocument.source_created_at,
        )
        .where(
            CanonicalDocument.tenant_id == tenant_uuid,
            CanonicalDocument.source_created_at >= start_dt,
            CanonicalDocument.source_created_at <= end_dt,
            CanonicalDocument.source_type.in_(["github", "jira", "slack"]),
        )
        .order_by(CanonicalDocument.source_created_at)
        .limit(200)
    )
    docs = doc_result.all()

    # ── 3. Build structured summaries ─────────────────────────────────────────
    # Incidents summary
    severity_counts: dict[str, int] = {}
    service_counts:  dict[str, int] = {}
    resolved_times:  list[float]    = []

    for inc in incidents:
        severity_counts[inc.severity] = severity_counts.get(inc.severity, 0) + 1
        svc = inc.service or "unknown"
        service_counts[svc] = service_counts.get(svc, 0) + 1
        if inc.resolved_at and inc.started_at:
            delta = (inc.resolved_at - inc.started_at).total_seconds() / 60
            resolved_times.append(delta)

    top_incidents = [
        {
            "title":      inc.title,
            "severity":   inc.severity,
            "service":    inc.service or "unknown",
            "started_at": inc.started_at.isoformat() if inc.started_at else None,
            "root_cause": inc.root_cause or "Not investigated",
        }
        for inc in sorted(incidents, key=lambda i: i.severity)[:10]
    ]

    incidents_summary = {
        "total":           len(incidents),
        "by_severity":     severity_counts,
        "by_service":      service_counts,
        "avg_resolution_minutes": round(sum(resolved_times) / len(resolved_times), 1) if resolved_times else None,
        "top_incidents":   top_incidents,
    }

    # Deploys / changes summary
    github_docs = [d for d in docs if d.source_type == "github"]
    jira_docs   = [d for d in docs if d.source_type == "jira"]

    deploys_summary = {
        "prs_merged":    len(github_docs),
        "jira_tickets":  len(jira_docs),
        "top_prs":       [{"title": d.title, "author": d.author, "url": d.url} for d in github_docs[:10]],
        "top_tickets":   [{"title": d.title, "author": d.author, "url": d.url} for d in jira_docs[:10]],
    }

    # Team patterns summary
    most_affected = sorted(service_counts.items(), key=lambda x: -x[1])
    authors       = {}
    for d in docs:
        if d.author:
            authors[d.author] = authors.get(d.author, 0) + 1
    top_contributors = sorted(authors.items(), key=lambda x: -x[1])[:5]

    recurring = [
        svc for svc, count in most_affected if count >= 2
    ]

    team_patterns = {
        "most_affected_services": [{"service": s, "incident_count": c} for s, c in most_affected[:5]],
        "recurring_services":     recurring,
        "top_contributors":       [{"author": a, "contributions": c} for a, c in top_contributors],
        "total_changes_shipped":  len(github_docs) + len(jira_docs),
    }

    # ── 4. LLM: generate recommendations ─────────────────────────────────────
    recommendations = await _generate_recommendations(
        incidents_summary=incidents_summary,
        deploys_summary=deploys_summary,
        team_patterns=team_patterns,
        period=f"{body.start_date} to {body.end_date}",
    )

    return RetrospectiveResponse(
        period=f"{body.start_date} to {body.end_date}",
        generated_at=datetime.now(tz=timezone.utc).isoformat(),
        incidents=incidents_summary,
        deploys=deploys_summary,
        team_patterns=team_patterns,
        recommendations=recommendations,
    )


async def _generate_recommendations(
    incidents_summary: dict,
    deploys_summary: dict,
    team_patterns: dict,
    period: str,
) -> list[str]:
    """
    Ask the LLM to produce concrete, actionable recommendations based on
    the retrospective data. Falls back to rule-based recommendations if
    the LLM call fails.
    """
    total_incidents = incidents_summary.get("total", 0)
    recurring       = team_patterns.get("recurring_services", [])
    avg_resolution  = incidents_summary.get("avg_resolution_minutes")
    prs_merged      = deploys_summary.get("prs_merged", 0)
    top_services    = team_patterns.get("most_affected_services", [])

    # Build context for the LLM
    context_lines = [
        f"Period: {period}",
        f"Total incidents: {total_incidents}",
        f"Severity breakdown: {incidents_summary.get('by_severity', {})}",
        f"Most affected services: {[s['service'] for s in top_services[:3]]}",
        f"Recurring problem services: {recurring}",
        f"Average resolution time: {avg_resolution} minutes" if avg_resolution else "Resolution time: unknown",
        f"PRs merged: {prs_merged}",
        f"Jira tickets closed: {deploys_summary.get('jira_tickets', 0)}",
    ]

    top_inc_text = "\n".join(
        f"- [{i['severity'].upper()}] {i['title']} ({i['service']}): {i['root_cause'][:100]}"
        for i in incidents_summary.get("top_incidents", [])[:5]
    )

    prompt = f"""You are an expert SRE coach writing an end-of-sprint retrospective for an engineering team.

SPRINT DATA:
{chr(10).join(context_lines)}

TOP INCIDENTS:
{top_inc_text or "No incidents this period."}

Generate exactly 5 concrete, actionable recommendations for the team based on this data.
Each recommendation should be specific — name the service, pattern, or practice.
Do not give generic advice. Reference the actual data above.

Respond ONLY as a JSON array of 5 strings, e.g.:
["Recommendation 1", "Recommendation 2", ...]"""

    try:
        if settings.LLM_PROVIDER == "ollama":
            from langchain_openai import ChatOpenAI
            llm = ChatOpenAI(
                model=settings.OLLAMA_CHAT_MODEL,
                base_url=f"{settings.OLLAMA_URL}/v1",
                api_key="ollama",
                temperature=0.3,
            )
        elif settings.LLM_PROVIDER == "claude":
            from langchain_anthropic import ChatAnthropic
            llm = ChatAnthropic(
                model=settings.ANTHROPIC_CHAT_MODEL,
                anthropic_api_key=settings.ANTHROPIC_API_KEY,
                temperature=0.3,
            )
        else:
            from langchain_openai import ChatOpenAI
            llm = ChatOpenAI(
                model=settings.OPENAI_CHAT_MODEL,
                api_key=settings.OPENAI_API_KEY,
                temperature=0.3,
            )

        from langchain_core.messages import HumanMessage
        response = await llm.ainvoke([HumanMessage(content=prompt)])
        content  = response.content.strip()

        # Extract JSON array from response
        import json, re
        match = re.search(r"\[.*\]", content, re.DOTALL)
        if match:
            recs = json.loads(match.group())
            if isinstance(recs, list) and all(isinstance(r, str) for r in recs):
                return recs[:5]

    except Exception as exc:
        logger.warning("LLM recommendations failed (%s) — using rule-based fallback", exc)

    # ── Rule-based fallback ────────────────────────────────────────────────────
    fallback: list[str] = []

    if recurring:
        fallback.append(
            f"Prioritise reliability work for {', '.join(recurring[:2])} — "
            f"{'these services' if len(recurring) > 1 else 'this service'} had repeated incidents this period."
        )
    if avg_resolution and avg_resolution > 60:
        fallback.append(
            f"Reduce mean time to resolution (currently {avg_resolution:.0f} min) by adding runbooks "
            f"for the top incident types and ensuring on-call engineers have direct Slack escalation paths."
        )
    if total_incidents > 5:
        worst = top_services[0]["service"] if top_services else "unknown"
        fallback.append(
            f"Conduct a root cause deep-dive for {worst} — it accounted for the most incidents "
            f"this period and may have a systemic underlying issue."
        )
    if prs_merged > 10:
        fallback.append(
            "Consider adding pre-deploy smoke tests or canary releases to reduce incident rate "
            "during high-velocity shipping weeks."
        )
    fallback.append(
        "Schedule a 30-minute incident review meeting to walk through the top 3 root causes "
        "and assign owners to the remediation action items."
    )

    return fallback[:5]
