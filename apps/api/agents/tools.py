"""
OpsLens AI — Agent Tool Library
=================================
Async callable tools used by the specialized agents.
Each tool wraps an existing service, DB query, or business logic function.

Tools are grouped by agent:
  research_tools  → hybrid retrieval, document search
  insight_tools   → pattern detection, insight queries
  alert_tools     → active alerts, severity summaries
  incident_tools  → RRT briefs, event timelines
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import sqlalchemy as sa

from ..utils.logging import get_logger

logger = get_logger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Research tools
# ══════════════════════════════════════════════════════════════════════════════

async def retrieve_documents(
    query: str,
    tenant_id: str,
    top_k: int = 8,
    allowed_sources: list[dict] | None = None,
) -> list[Any]:
    """Hybrid BM25 + dense vector retrieval for a query."""
    from ..services.hybrid_retriever import hybrid_retrieve
    try:
        docs = await hybrid_retrieve(
            query, tenant_id, top_k=top_k, allowed_sources=allowed_sources
        )
        return docs
    except Exception as exc:
        logger.warning("retrieve_documents failed: %s", exc)
        return []


# ══════════════════════════════════════════════════════════════════════════════
# Insight tools
# ══════════════════════════════════════════════════════════════════════════════

async def get_recent_insights(
    tenant_id: str,
    insight_type: str | None = None,
    limit: int = 10,
) -> list[dict]:
    """Fetch recently generated insights from the database."""
    from ..db.session import get_db_context
    from ..models.insight import Insight

    try:
        from ..db.session import AsyncSession
        async with AsyncSession() as db:
            query = (
                sa.select(Insight)
                .where(
                    Insight.tenant_id == tenant_id,
                    Insight.status == "active",
                )
                .order_by(Insight.generated_at.desc())
                .limit(limit)
            )
            if insight_type:
                query = query.where(Insight.insight_type == insight_type)

            result = await db.execute(query)
            insights = result.scalars().all()
            return [
                {
                    "id":           str(i.id),
                    "type":         i.insight_type,
                    "title":        i.title,
                    "summary":      i.summary,
                    "magnitude":    i.magnitude,
                    "confidence":   (i.raw_data or {}).get("confidence", "medium"),
                    "generated_at": i.generated_at.isoformat() if i.generated_at else None,
                }
                for i in insights
            ]
    except Exception as exc:
        logger.warning("get_recent_insights failed: %s", exc)
        return []


async def run_insight_detector_now(
    tenant_id: str,
    detector_name: str,
) -> dict | None:
    """Run a specific insight detector synchronously and return the result."""
    from ...worker.tasks.insight_engine import ALL_DETECTORS
    cls = ALL_DETECTORS.get(detector_name)
    if not cls:
        return None
    try:
        detector = cls()
        detected = await detector.run(tenant_id)
        if not detected:
            return None
        return {
            "title":        detected.title,
            "summary":      detected.summary,
            "magnitude":    detected.magnitude,
            "insight_type": detected.insight_type,
            "confidence":   detected.confidence,
        }
    except Exception as exc:
        logger.warning("run_insight_detector_now(%s) failed: %s", detector_name, exc)
        return None


# ══════════════════════════════════════════════════════════════════════════════
# Alert tools
# ══════════════════════════════════════════════════════════════════════════════

async def get_active_alerts(
    tenant_id: str,
    severity: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """Fetch active (unacknowledged) alerts for a tenant."""
    from ..db.session import get_db_context
    from ..models.alert import Alert

    try:
        from ..db.session import AsyncSession
        async with AsyncSession() as db:
            query = (
                sa.select(Alert)
                .where(
                    Alert.tenant_id == tenant_id,
                    Alert.status.in_(["active", "firing"]),
                )
                .order_by(Alert.fired_at.desc())
                .limit(limit)
            )
            if severity:
                query = query.where(Alert.severity == severity)

            result = await db.execute(query)
            alerts = result.scalars().all()
            return [
                {
                    "id":          str(a.id),
                    "title":       a.title,
                    "severity":    a.severity,
                    "status":      a.status,
                    "source_type": a.source_type,
                    "message":     a.message,
                    "fired_at":    a.fired_at.isoformat() if a.fired_at else None,
                }
                for a in alerts
            ]
    except Exception as exc:
        logger.warning("get_active_alerts failed: %s", exc)
        return []


async def get_alert_summary(tenant_id: str, hours: int = 24) -> dict:
    """Return aggregated alert counts by severity over the past N hours."""
    from ..db.session import get_db_context
    from ..models.alert import Alert

    try:
        from ..db.session import AsyncSession
        async with AsyncSession() as db:
            cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=hours)
            result = await db.execute(
                sa.select(Alert.severity, sa.func.count(Alert.id).label("n"))
                .where(
                    Alert.tenant_id == tenant_id,
                    Alert.fired_at >= cutoff,
                )
                .group_by(Alert.severity)
            )
            counts = {row.severity: row.n for row in result}
            return {
                "window_hours": hours,
                "by_severity":  counts,
                "total":        sum(counts.values()),
            }
    except Exception as exc:
        logger.warning("get_alert_summary failed: %s", exc)
        return {"window_hours": hours, "by_severity": {}, "total": 0}


# ══════════════════════════════════════════════════════════════════════════════
# Incident tools
# ══════════════════════════════════════════════════════════════════════════════

async def get_recent_rrt_briefs(
    tenant_id: str,
    limit: int = 5,
) -> list[dict]:
    """Fetch the most recent RRT (Rapid Response Team) incident briefs."""
    from ..db.session import get_db_context
    from ..models.rrt_brief import RRTBrief

    try:
        from ..db.session import AsyncSession
        async with AsyncSession() as db:
            result = await db.execute(
                sa.select(RRTBrief)
                .where(RRTBrief.tenant_id == tenant_id)
                .order_by(RRTBrief.created_at.desc())
                .limit(limit)
            )
            briefs = result.scalars().all()
            return [
                {
                    "id":          str(b.id),
                    "title":       b.title,
                    "summary":     b.summary,
                    "severity":    b.severity,
                    "status":      b.status,
                    "created_at":  b.created_at.isoformat() if b.created_at else None,
                }
                for b in briefs
            ]
    except Exception as exc:
        logger.warning("get_recent_rrt_briefs failed: %s", exc)
        return []


async def get_incident_timeline(
    tenant_id: str,
    hours: int = 24,
) -> list[dict]:
    """Fetch recent timeline events (deploys, PRs, alerts) for incident context."""
    from ..db.session import get_db_context
    from ..models.timeline import TimelineEvent

    try:
        from ..db.session import AsyncSession
        async with AsyncSession() as db:
            cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=hours)
            result = await db.execute(
                sa.select(TimelineEvent)
                .where(
                    TimelineEvent.tenant_id == tenant_id,
                    TimelineEvent.occurred_at >= cutoff,
                )
                .order_by(TimelineEvent.occurred_at.desc())
                .limit(50)
            )
            events = result.scalars().all()
            return [
                {
                    "id":          str(e.id),
                    "event_type":  e.event_type,
                    "title":       e.title,
                    "description": e.description,
                    "actor":       e.actor,
                    "occurred_at": e.occurred_at.isoformat() if e.occurred_at else None,
                }
                for e in events
            ]
    except Exception as exc:
        logger.warning("get_incident_timeline failed: %s", exc)
        return []
