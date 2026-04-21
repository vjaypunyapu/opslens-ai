"""
OpsLens AI — Incidents Router
================================
Manages production incident investigations with AI-powered root cause analysis.

Endpoints:
    POST   /api/v1/incidents                    — Create + auto-investigate
    GET    /api/v1/incidents                    — List incidents
    GET    /api/v1/incidents/{id}               — Get incident with timeline + RCA
    PATCH  /api/v1/incidents/{id}               — Update status / severity
    DELETE /api/v1/incidents/{id}               — Delete
    POST   /api/v1/incidents/{id}/investigate   — Re-trigger investigation
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Annotated, Literal

import sqlalchemy as sa
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from ..auth.dependencies import TenantContext, require_member, require_viewer
from ..db.models import Incident
from ..db.session import get_db
from ..utils.logging import get_logger

router = APIRouter()
logger = get_logger(__name__)


# ── Schemas ───────────────────────────────────────────────────────────────────

SeverityLiteral = Literal["p0", "p1", "p2", "p3", "p4"]
StatusLiteral   = Literal["open", "investigating", "analysing", "resolved", "closed"]


class IncidentCreateBody(BaseModel):
    title: str = Field(..., min_length=3, max_length=512)
    description: str | None = Field(None, max_length=4000)
    service: str | None = Field(None, max_length=255)
    severity: SeverityLiteral = "p2"
    started_at: datetime | None = None


class IncidentUpdateBody(BaseModel):
    title: str | None = Field(None, min_length=3, max_length=512)
    status: StatusLiteral | None = None
    severity: SeverityLiteral | None = None
    resolved_at: datetime | None = None


class TimelineEvent(BaseModel):
    timestamp: str
    source: str
    source_type: str
    event_type: str
    title: str
    detail: str
    url: str | None = None
    severity: str | None = None


class IncidentOut(BaseModel):
    id: uuid.UUID
    title: str
    description: str | None
    status: str
    severity: str
    service: str | None
    started_at: str | None
    resolved_at: str | None
    root_cause: str | None
    contributing_factors: list[str]
    recommendations: list[str]
    timeline: list[dict]
    signals: list[dict]
    created_by: str | None
    created_at: str
    updated_at: str


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("", response_model=IncidentOut, status_code=status.HTTP_201_CREATED)
async def create_incident(
    body: IncidentCreateBody,
    ctx: Annotated[TenantContext, Depends(require_member)],
    db=Depends(get_db),
):
    """Create a new incident. Call POST /{id}/investigate to trigger AI investigation."""
    incident = Incident(
        tenant_id=ctx.tenant_id,
        title=body.title,
        description=body.description,
        service=body.service,
        severity=body.severity,
        started_at=body.started_at or datetime.now(timezone.utc),
        status="open",
        created_by=ctx.user_id,
    )
    db.add(incident)
    await db.commit()
    await db.refresh(incident)

    logger.info("Created incident %s for tenant %s", incident.id, ctx.tenant_id)

    return _to_out(incident)


@router.get("", response_model=list[IncidentOut])
async def list_incidents(
    ctx: Annotated[TenantContext, Depends(require_viewer)],
    db=Depends(get_db),
    status_filter: str | None = Query(None, alias="status"),
    severity: str | None = None,
    limit: int = Query(20, le=100),
    offset: int = 0,
):
    q = sa.select(Incident).where(Incident.tenant_id == ctx.tenant_id)
    if status_filter:
        q = q.where(Incident.status == status_filter)
    if severity:
        q = q.where(Incident.severity == severity)
    q = q.order_by(Incident.created_at.desc()).limit(limit).offset(offset)
    result = await db.execute(q)
    return [_to_out(i) for i in result.scalars().all()]


@router.get("/{incident_id}", response_model=IncidentOut)
async def get_incident(
    incident_id: uuid.UUID,
    ctx: Annotated[TenantContext, Depends(require_viewer)],
    db=Depends(get_db),
):
    incident = await _get_or_404(db, incident_id, ctx.tenant_id)
    return _to_out(incident)


@router.patch("/{incident_id}", response_model=IncidentOut)
async def update_incident(
    incident_id: uuid.UUID,
    body: IncidentUpdateBody,
    ctx: Annotated[TenantContext, Depends(require_member)],
    db=Depends(get_db),
):
    incident = await _get_or_404(db, incident_id, ctx.tenant_id)
    if body.title is not None:
        incident.title = body.title
    if body.status is not None:
        incident.status = body.status
        if body.status == "resolved" and not incident.resolved_at:
            incident.resolved_at = datetime.now(timezone.utc)
    if body.severity is not None:
        incident.severity = body.severity
    if body.resolved_at is not None:
        incident.resolved_at = body.resolved_at
    await db.commit()
    await db.refresh(incident)
    return _to_out(incident)


@router.delete("/{incident_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_incident(
    incident_id: uuid.UUID,
    ctx: Annotated[TenantContext, Depends(require_member)],
    db=Depends(get_db),
):
    incident = await _get_or_404(db, incident_id, ctx.tenant_id)
    await db.delete(incident)
    await db.commit()


@router.post("/{incident_id}/investigate", response_model=IncidentOut)
async def reinvestigate(
    incident_id: uuid.UUID,
    ctx: Annotated[TenantContext, Depends(require_member)],
    background_tasks: BackgroundTasks,
    db=Depends(get_db),
):
    """Re-trigger the AI investigation for an existing incident."""
    incident = await _get_or_404(db, incident_id, ctx.tenant_id)
    incident.status = "investigating"
    incident.root_cause = None
    incident.timeline = []
    incident.signals = []
    await db.commit()
    await db.refresh(incident)   # re-hydrate after commit so _to_out can read attributes

    background_tasks.add_task(
        _trigger_investigation, str(incident.id), str(ctx.tenant_id), ctx.company_name
    )
    return _to_out(incident)


# ── Background investigation ──────────────────────────────────────────────────

async def _trigger_investigation(incident_id: str, tenant_id: str, company_name: str):
    """Dispatch the Celery investigation task."""
    try:
        from ..services.incident_service import investigate_incident
        await investigate_incident(incident_id, tenant_id, company_name)
    except Exception as exc:
        logger.exception("Investigation failed for incident %s: %s", incident_id, exc)


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_or_404(db, incident_id: uuid.UUID, tenant_id: uuid.UUID) -> Incident:
    result = await db.execute(
        sa.select(Incident).where(
            Incident.id == incident_id,
            Incident.tenant_id == tenant_id,
        )
    )
    inc = result.scalar_one_or_none()
    if not inc:
        raise HTTPException(status_code=404, detail="Incident not found.")
    return inc


def _to_out(i: Incident) -> IncidentOut:
    return IncidentOut(
        id=i.id,
        title=i.title,
        description=i.description,
        status=i.status,
        severity=i.severity,
        service=i.service,
        started_at=i.started_at.isoformat() if i.started_at else None,
        resolved_at=i.resolved_at.isoformat() if i.resolved_at else None,
        root_cause=i.root_cause,
        contributing_factors=i.contributing_factors or [],
        recommendations=i.recommendations or [],
        timeline=i.timeline or [],
        signals=i.signals or [],
        created_by=i.created_by,
        created_at=i.created_at.isoformat(),
        updated_at=i.updated_at.isoformat(),
    )
