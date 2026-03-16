"""
OpsLens AI — Dashboard Router
================================
Aggregated KPI and activity data for the workspace home page.

Endpoints:
    GET /api/v1/dashboard  — Summary stats + recent activity
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends

from ..auth.dependencies import TenantContext, require_viewer
from ..db.models import AlertRule, CanonicalDocument, ChatSession, Insight, Integration
from ..db.session import get_db
from ..utils.logging import get_logger

router = APIRouter()
logger = get_logger(__name__)


@router.get("/dashboard")
async def get_dashboard(
    ctx: Annotated[TenantContext, Depends(require_viewer)],
    db=Depends(get_db),
):
    """Return all KPIs and recent activity for the workspace dashboard."""
    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)
    day_ago  = now - timedelta(days=1)

    tid = ctx.tenant_id

    # ── Insights ──────────────────────────────────────────────────────────────
    ins_rows = (await db.execute(
        sa.select(Insight.status, sa.func.count(Insight.id).label("n"))
        .where(Insight.tenant_id == tid)
        .group_by(Insight.status)
    )).all()
    ins_counts = {r.status: r.n for r in ins_rows}

    new_insights_7d = (await db.execute(
        sa.select(sa.func.count(Insight.id))
        .where(Insight.tenant_id == tid, Insight.generated_at >= week_ago)
    )).scalar() or 0

    recent_insights = (await db.execute(
        sa.select(Insight)
        .where(Insight.tenant_id == tid)
        .order_by(Insight.generated_at.desc())
        .limit(5)
    )).scalars().all()

    # ── Integrations ──────────────────────────────────────────────────────────
    integrations = (await db.execute(
        sa.select(Integration).where(Integration.tenant_id == tid)
    )).scalars().all()

    # ── Documents ─────────────────────────────────────────────────────────────
    doc_rows = (await db.execute(
        sa.select(
            CanonicalDocument.source_type,
            sa.func.count(CanonicalDocument.id).label("n"),
        )
        .where(CanonicalDocument.tenant_id == tid)
        .group_by(CanonicalDocument.source_type)
    )).all()
    docs_by_source = {r.source_type: r.n for r in doc_rows}
    total_docs = sum(docs_by_source.values())

    docs_indexed_7d = (await db.execute(
        sa.select(sa.func.count(CanonicalDocument.id))
        .where(CanonicalDocument.tenant_id == tid, CanonicalDocument.created_at >= week_ago)
    )).scalar() or 0

    # ── Chat sessions ─────────────────────────────────────────────────────────
    total_sessions = (await db.execute(
        sa.select(sa.func.count(ChatSession.id))
        .where(ChatSession.tenant_id == tid)
    )).scalar() or 0

    sessions_7d = (await db.execute(
        sa.select(sa.func.count(ChatSession.id))
        .where(ChatSession.tenant_id == tid, ChatSession.created_at >= week_ago)
    )).scalar() or 0

    recent_sessions = (await db.execute(
        sa.select(ChatSession)
        .where(ChatSession.tenant_id == tid)
        .order_by(ChatSession.updated_at.desc())
        .limit(5)
    )).scalars().all()

    # ── Alerts ────────────────────────────────────────────────────────────────
    active_alert_rules = (await db.execute(
        sa.select(sa.func.count(AlertRule.id))
        .where(AlertRule.tenant_id == tid, AlertRule.is_active == True)  # noqa: E712
    )).scalar() or 0

    return {
        "insights": {
            "active":      ins_counts.get("active", 0),
            "resolved":    ins_counts.get("resolved", 0),
            "snoozed":     ins_counts.get("snoozed", 0),
            "new_7d":      new_insights_7d,
            "recent": [
                {
                    "id":           str(i.id),
                    "title":        i.title,
                    "insight_type": i.insight_type,
                    "magnitude":    i.magnitude,
                    "status":       i.status,
                    "generated_at": i.generated_at.isoformat(),
                }
                for i in recent_insights
            ],
        },
        "documents": {
            "total":       total_docs,
            "indexed_7d":  docs_indexed_7d,
            "by_source":   docs_by_source,
        },
        "integrations": {
            "total":   len(integrations),
            "active":  sum(1 for i in integrations if i.status == "active"),
            "sources": [
                {
                    "id":            str(i.id),
                    "source_type":   i.source_type,
                    "status":        i.status,
                    "last_synced_at": i.last_synced_at.isoformat() if i.last_synced_at else None,
                }
                for i in integrations
            ],
        },
        "chat": {
            "total_sessions": total_sessions,
            "sessions_7d":    sessions_7d,
            "recent_sessions": [
                {
                    "id":         str(s.id),
                    "title":      s.title or "New conversation",
                    "updated_at": s.updated_at.isoformat(),
                }
                for s in recent_sessions
            ],
        },
        "alerts": {
            "active_rules": active_alert_rules,
        },
        "generated_at": now.isoformat(),
    }
