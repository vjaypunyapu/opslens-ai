"""
OpsLens AI — Manager Dashboard Router
======================================
Aggregated views for engineering managers and VPs:

  GET /api/v1/manager/summary              — rolling window KPI snapshot
  GET /api/v1/manager/delivery-risk        — open incidents + at-risk deployments
  GET /api/v1/manager/blocked-initiatives  — Jira issues stuck in a status > N days
  GET /api/v1/manager/deployment-frequency — deploys per service per day/week
  GET /api/v1/manager/team-health          — per-team incident + alert load

All endpoints require role=manager|admin in the JWT claims.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.auth.middleware import require_roles
from apps.api.db.session import get_db
from apps.api.models.rrt_brief import RRTBrief
from apps.api.models.timeline import TimelineEvent

router = APIRouter()


# ── Pydantic response schemas ─────────────────────────────────────────────────

class KPIBlock(BaseModel):
    label: str
    value: Any
    unit: str | None = None
    delta_pct: float | None = None      # % change vs previous period
    trend: Literal["up", "down", "flat"] | None = None


class SummaryResponse(BaseModel):
    window_days: int
    since: str
    until: str
    kpis: list[KPIBlock]
    top_affected_services: list[dict]
    open_incidents: int
    resolved_incidents: int


class DeliveryRiskItem(BaseModel):
    brief_id: str
    title: str
    severity: str | None
    status: str
    service: str | None
    team: str | None
    detected_at: str
    age_hours: float
    recent_deploys: int           # deploys in same service in last 24h


class DeliveryRiskResponse(BaseModel):
    open_briefs: list[DeliveryRiskItem]
    change_failure_rate_pct: float | None  # (incidents w/ recent deploy) / total deploys * 100
    deployments_today: int


class BlockedInitiativeItem(BaseModel):
    source_id: str               # Jira issue key (e.g. "OPS-142")
    title: str
    service: str | None
    team: str | None
    status_stuck: str            # the Jira status it's been stuck in
    stuck_since: str             # ISO timestamp of last transition
    stuck_days: float


class BlockedInitiativesResponse(BaseModel):
    items: list[BlockedInitiativeItem]
    threshold_days: int


class DeployFrequencyRow(BaseModel):
    service: str
    date: str                    # YYYY-MM-DD
    deploy_count: int


class DeployFrequencyResponse(BaseModel):
    window_days: int
    rows: list[DeployFrequencyRow]
    total_deploys: int
    services: list[str]


class TeamHealthRow(BaseModel):
    team: str
    open_incidents: int
    resolved_incidents: int
    mttr_hours: float | None
    alerts_fired: int            # log_anomaly events
    top_service: str | None


class TeamHealthResponse(BaseModel):
    window_days: int
    rows: list[TeamHealthRow]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _delta_pct(current: float, previous: float) -> float | None:
    if previous == 0:
        return None
    return round((current - previous) / previous * 100, 1)


def _trend(delta: float | None) -> str | None:
    if delta is None:
        return None
    if delta > 2:
        return "up"
    if delta < -2:
        return "down"
    return "flat"


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get(
    "/summary",
    response_model=SummaryResponse,
    summary="Weekly/rolling KPI snapshot for engineering managers",
)
async def get_manager_summary(
    window_days: int = Query(7, ge=1, le=90, description="Rolling look-back window in days"),
    tenant_id: str = Query(..., description="Tenant identifier"),
    db: AsyncSession = Depends(get_db),
    _auth=Depends(require_roles(["manager", "admin", "viewer"])),
):
    """
    Returns key performance indicators for the chosen window:

    - Total incidents (RRT Briefs opened)
    - Resolved incidents
    - Mean Time to Resolve (MTTR) in hours
    - Total deployments recorded via GitHub webhooks
    - Top 5 services by incident count
    """
    now = _now_utc()
    since = now - timedelta(days=window_days)
    prev_since = since - timedelta(days=window_days)     # previous period for delta

    # ── current period ────────────────────────────────────────────────────────
    briefs_q = await db.execute(
        sa.select(RRTBrief).where(
            RRTBrief.tenant_id == tenant_id,
            RRTBrief.detected_at >= since,
        )
    )
    briefs: list[RRTBrief] = list(briefs_q.scalars().all())

    open_count = sum(1 for b in briefs if b.status not in ("resolved",))
    resolved_count = sum(1 for b in briefs if b.status == "resolved")
    total_count = len(briefs)

    # MTTR — average hours from detected_at → resolved_at for resolved briefs
    mttr_hours: float | None = None
    resolved_with_time = [
        b for b in briefs
        if b.status == "resolved" and b.resolved_at and b.detected_at
    ]
    if resolved_with_time:
        durations = [
            (b.resolved_at - b.detected_at).total_seconds() / 3600
            for b in resolved_with_time
        ]
        mttr_hours = round(sum(durations) / len(durations), 1)

    # Deploys in window
    deploys_q = await db.execute(
        sa.select(sa.func.count()).where(
            TimelineEvent.tenant_id == tenant_id,
            TimelineEvent.source_type == "github_deploy",
            TimelineEvent.occurred_at >= since,
        )
    )
    deploy_count: int = deploys_q.scalar_one() or 0

    # ── previous period for deltas ────────────────────────────────────────────
    prev_briefs_q = await db.execute(
        sa.select(sa.func.count()).where(
            RRTBrief.tenant_id == tenant_id,
            RRTBrief.detected_at >= prev_since,
            RRTBrief.detected_at < since,
        )
    )
    prev_total: int = prev_briefs_q.scalar_one() or 0

    prev_deploy_q = await db.execute(
        sa.select(sa.func.count()).where(
            TimelineEvent.tenant_id == tenant_id,
            TimelineEvent.source_type == "github_deploy",
            TimelineEvent.occurred_at >= prev_since,
            TimelineEvent.occurred_at < since,
        )
    )
    prev_deploys: int = prev_deploy_q.scalar_one() or 0

    # ── top affected services ─────────────────────────────────────────────────
    service_counts: dict[str, int] = {}
    for b in briefs:
        svc = b.service or "unknown"
        service_counts[svc] = service_counts.get(svc, 0) + 1
    top_services = sorted(service_counts.items(), key=lambda x: x[1], reverse=True)[:5]

    # ── assemble KPIs ─────────────────────────────────────────────────────────
    inc_delta = _delta_pct(total_count, prev_total)
    dep_delta = _delta_pct(deploy_count, prev_deploys)

    kpis = [
        KPIBlock(
            label="Total Incidents",
            value=total_count,
            delta_pct=inc_delta,
            trend=_trend(inc_delta),
        ),
        KPIBlock(
            label="Resolved",
            value=resolved_count,
        ),
        KPIBlock(
            label="Open / Active",
            value=open_count,
        ),
        KPIBlock(
            label="MTTR",
            value=mttr_hours,
            unit="hours",
        ),
        KPIBlock(
            label="Deployments",
            value=deploy_count,
            delta_pct=dep_delta,
            trend=_trend(dep_delta),
        ),
    ]

    return SummaryResponse(
        window_days=window_days,
        since=since.isoformat(),
        until=now.isoformat(),
        kpis=kpis,
        top_affected_services=[
            {"service": svc, "incident_count": cnt} for svc, cnt in top_services
        ],
        open_incidents=open_count,
        resolved_incidents=resolved_count,
    )


@router.get(
    "/delivery-risk",
    response_model=DeliveryRiskResponse,
    summary="Open incidents enriched with recent deploy activity",
)
async def get_delivery_risk(
    tenant_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
    _auth=Depends(require_roles(["manager", "admin", "viewer"])),
):
    """
    Returns all currently open RRT Briefs, each enriched with how many
    deployments occurred in the same service in the 24 hours before the incident.
    Also computes a rough Change Failure Rate.
    """
    now = _now_utc()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Open briefs
    open_q = await db.execute(
        sa.select(RRTBrief).where(
            RRTBrief.tenant_id == tenant_id,
            RRTBrief.status.notin_(["resolved"]),
        ).order_by(RRTBrief.detected_at.desc())
    )
    open_briefs: list[RRTBrief] = list(open_q.scalars().all())

    # Deploys today (for the header metric)
    deploys_today_q = await db.execute(
        sa.select(sa.func.count()).where(
            TimelineEvent.tenant_id == tenant_id,
            TimelineEvent.source_type == "github_deploy",
            TimelineEvent.occurred_at >= today_start,
        )
    )
    deploys_today: int = deploys_today_q.scalar_one() or 0

    # Change Failure Rate — briefs in last 30d that had at least one deploy in 24h before
    thirty_ago = now - timedelta(days=30)
    all_recent_q = await db.execute(
        sa.select(RRTBrief).where(
            RRTBrief.tenant_id == tenant_id,
            RRTBrief.detected_at >= thirty_ago,
        )
    )
    all_recent = list(all_recent_q.scalars().all())

    # Deploys in last 30d
    deploys_30d_q = await db.execute(
        sa.select(sa.func.count()).where(
            TimelineEvent.tenant_id == tenant_id,
            TimelineEvent.source_type == "github_deploy",
            TimelineEvent.occurred_at >= thirty_ago,
        )
    )
    deploys_30d: int = deploys_30d_q.scalar_one() or 0

    # For each open brief, count deploys in same service in 24h before incident
    risk_items: list[DeliveryRiskItem] = []
    for brief in open_briefs:
        window_start = brief.detected_at - timedelta(hours=24)
        deploy_near_q = await db.execute(
            sa.select(sa.func.count()).where(
                TimelineEvent.tenant_id == tenant_id,
                TimelineEvent.source_type == "github_deploy",
                TimelineEvent.occurred_at >= window_start,
                TimelineEvent.occurred_at <= brief.detected_at,
                *([TimelineEvent.service == brief.service] if brief.service else []),
            )
        )
        near_deploys: int = deploy_near_q.scalar_one() or 0

        age_hours = (now - brief.detected_at).total_seconds() / 3600

        risk_items.append(DeliveryRiskItem(
            brief_id=str(brief.id),
            title=brief.title or "Untitled Incident",
            severity=brief.severity,
            status=brief.status,
            service=brief.service,
            team=brief.team,
            detected_at=brief.detected_at.isoformat(),
            age_hours=round(age_hours, 1),
            recent_deploys=near_deploys,
        ))

    # Approximate CFR — incidents with ≥1 near deploy / total deploys
    incidents_with_deploy = 0
    for brief in all_recent:
        if brief.detected_at:
            window_start = brief.detected_at - timedelta(hours=24)
            chk_q = await db.execute(
                sa.select(sa.func.count()).where(
                    TimelineEvent.tenant_id == tenant_id,
                    TimelineEvent.source_type == "github_deploy",
                    TimelineEvent.occurred_at >= window_start,
                    TimelineEvent.occurred_at <= brief.detected_at,
                )
            )
            if (chk_q.scalar_one() or 0) > 0:
                incidents_with_deploy += 1

    cfr: float | None = None
    if deploys_30d > 0:
        cfr = round(incidents_with_deploy / deploys_30d * 100, 1)

    return DeliveryRiskResponse(
        open_briefs=risk_items,
        change_failure_rate_pct=cfr,
        deployments_today=deploys_today,
    )


@router.get(
    "/blocked-initiatives",
    response_model=BlockedInitiativesResponse,
    summary="Jira issues stuck in a status for longer than threshold",
)
async def get_blocked_initiatives(
    tenant_id: str = Query(...),
    threshold_days: int = Query(3, ge=1, le=30, description="Days in same status to flag as blocked"),
    db: AsyncSession = Depends(get_db),
    _auth=Depends(require_roles(["manager", "admin", "viewer"])),
):
    """
    Queries the timeline for Jira issue transitions and identifies issues
    that have not moved from their current status in > threshold_days.
    Returns the most recent transition per issue key and flags stale ones.
    """
    now = _now_utc()
    look_back = now - timedelta(days=90)     # only look at last 90d of Jira data

    jira_q = await db.execute(
        sa.select(TimelineEvent).where(
            TimelineEvent.tenant_id == tenant_id,
            TimelineEvent.source_type == "jira_issue",
            TimelineEvent.occurred_at >= look_back,
        ).order_by(TimelineEvent.occurred_at.asc())
    )
    jira_events: list[TimelineEvent] = list(jira_q.scalars().all())

    # Build latest-state map per issue key
    # source_id pattern: "jira:<issue_key>:<status>"
    latest: dict[str, TimelineEvent] = {}
    for ev in jira_events:
        # source_id = "jira:OPS-142:In Progress" (set in timeline router)
        parts = (ev.source_id or "").split(":", 2)
        if len(parts) >= 2:
            issue_key = parts[1]
            latest[issue_key] = ev      # last write wins (events sorted ascending)

    blocked: list[BlockedInitiativeItem] = []
    cutoff = now - timedelta(days=threshold_days)

    for issue_key, ev in latest.items():
        if ev.occurred_at <= cutoff:
            # Stuck status = the description after last colon in source_id, or event title
            parts = (ev.source_id or "").split(":", 2)
            stuck_status = parts[2] if len(parts) >= 3 else (ev.description or "unknown")

            stuck_days = (now - ev.occurred_at).total_seconds() / 86400

            blocked.append(BlockedInitiativeItem(
                source_id=issue_key,
                title=ev.title or issue_key,
                service=ev.service,
                team=ev.team,
                status_stuck=stuck_status,
                stuck_since=ev.occurred_at.isoformat(),
                stuck_days=round(stuck_days, 1),
            ))

    # Sort by stuck_days descending (worst first)
    blocked.sort(key=lambda x: x.stuck_days, reverse=True)

    return BlockedInitiativesResponse(
        items=blocked,
        threshold_days=threshold_days,
    )


@router.get(
    "/deployment-frequency",
    response_model=DeployFrequencyResponse,
    summary="Deployments per service per day over a rolling window",
)
async def get_deployment_frequency(
    tenant_id: str = Query(...),
    window_days: int = Query(14, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
    _auth=Depends(require_roles(["manager", "admin", "viewer"])),
):
    """
    Returns a time-series of deployment counts per service.
    Useful for tracking deployment cadence and spotting dry periods.
    """
    now = _now_utc()
    since = now - timedelta(days=window_days)

    deploy_q = await db.execute(
        sa.select(TimelineEvent).where(
            TimelineEvent.tenant_id == tenant_id,
            TimelineEvent.source_type == "github_deploy",
            TimelineEvent.occurred_at >= since,
        ).order_by(TimelineEvent.occurred_at.asc())
    )
    deploys: list[TimelineEvent] = list(deploy_q.scalars().all())

    # Bucket by service + date
    bucket: dict[tuple[str, str], int] = {}
    services: set[str] = set()
    for ev in deploys:
        svc = ev.service or "unknown"
        day = ev.occurred_at.strftime("%Y-%m-%d")
        bucket[(svc, day)] = bucket.get((svc, day), 0) + 1
        services.add(svc)

    rows = [
        DeployFrequencyRow(service=svc, date=day, deploy_count=cnt)
        for (svc, day), cnt in sorted(bucket.items(), key=lambda x: x[0][1])
    ]

    return DeployFrequencyResponse(
        window_days=window_days,
        rows=rows,
        total_deploys=len(deploys),
        services=sorted(services),
    )


@router.get(
    "/team-health",
    response_model=TeamHealthResponse,
    summary="Per-team incident load, MTTR, and alert volume",
)
async def get_team_health(
    tenant_id: str = Query(...),
    window_days: int = Query(7, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
    _auth=Depends(require_roles(["manager", "admin", "viewer"])),
):
    """
    Aggregates RRT Briefs and log_anomaly timeline events by team, showing
    which teams are under the most incident pressure.
    """
    now = _now_utc()
    since = now - timedelta(days=window_days)

    briefs_q = await db.execute(
        sa.select(RRTBrief).where(
            RRTBrief.tenant_id == tenant_id,
            RRTBrief.detected_at >= since,
        )
    )
    briefs: list[RRTBrief] = list(briefs_q.scalars().all())

    anomalies_q = await db.execute(
        sa.select(TimelineEvent).where(
            TimelineEvent.tenant_id == tenant_id,
            TimelineEvent.source_type == "log_anomaly",
            TimelineEvent.occurred_at >= since,
        )
    )
    anomalies: list[TimelineEvent] = list(anomalies_q.scalars().all())

    # Build per-team structures
    team_open: dict[str, int] = {}
    team_resolved: dict[str, int] = {}
    team_mttr_data: dict[str, list[float]] = {}
    team_services: dict[str, dict[str, int]] = {}

    for b in briefs:
        t = b.team or "unassigned"
        if b.status == "resolved":
            team_resolved[t] = team_resolved.get(t, 0) + 1
            if b.resolved_at and b.detected_at:
                hrs = (b.resolved_at - b.detected_at).total_seconds() / 3600
                team_mttr_data.setdefault(t, []).append(hrs)
        else:
            team_open[t] = team_open.get(t, 0) + 1

        if b.service:
            svc_map = team_services.setdefault(t, {})
            svc_map[b.service] = svc_map.get(b.service, 0) + 1

    team_alerts: dict[str, int] = {}
    for a in anomalies:
        t = a.team or "unassigned"
        team_alerts[t] = team_alerts.get(t, 0) + 1

    all_teams = set(list(team_open) + list(team_resolved) + list(team_alerts))

    rows: list[TeamHealthRow] = []
    for team in sorted(all_teams):
        mttr_list = team_mttr_data.get(team, [])
        mttr = round(sum(mttr_list) / len(mttr_list), 1) if mttr_list else None

        svc_map = team_services.get(team, {})
        top_svc = max(svc_map, key=svc_map.get) if svc_map else None

        rows.append(TeamHealthRow(
            team=team,
            open_incidents=team_open.get(team, 0),
            resolved_incidents=team_resolved.get(team, 0),
            mttr_hours=mttr,
            alerts_fired=team_alerts.get(team, 0),
            top_service=top_svc,
        ))

    # Sort by total pressure (open + alerts)
    rows.sort(key=lambda r: r.open_incidents + r.alerts_fired, reverse=True)

    return TeamHealthResponse(window_days=window_days, rows=rows)
