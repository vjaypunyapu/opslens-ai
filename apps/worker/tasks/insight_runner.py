"""
OpsLens AI – Insight Generation Engine
Runs on a schedule (Celery Beat) to detect operational patterns and persist insights.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import sqlalchemy as sa
from celery import shared_task
from celery.utils.log import get_task_logger
from langchain_core.prompts import ChatPromptTemplate
from ...api.config import settings
from ..db import AsyncSession
from ..models.document import CanonicalDocument
from ..models.insight import Insight
from ..async_utils import run_async as _run_async

logger = get_task_logger(__name__)

# ── LLM: respects LLM_PROVIDER setting ────────────────────────────────────────
if settings.LLM_PROVIDER == "ollama":
    from langchain_openai import ChatOpenAI
    _llm = ChatOpenAI(
        model=settings.OLLAMA_CHAT_MODEL,
        base_url=f"{settings.OLLAMA_URL}/v1",
        api_key="ollama",
        temperature=0.1,
        timeout=300,
    )
elif settings.LLM_PROVIDER == "claude":
    from langchain_anthropic import ChatAnthropic
    _llm = ChatAnthropic(
        model=settings.ANTHROPIC_CHAT_MODEL,
        anthropic_api_key=settings.ANTHROPIC_API_KEY,
        temperature=0.1,
    )
else:
    from langchain_openai import ChatOpenAI
    _llm = ChatOpenAI(
        model=settings.OPENAI_CHAT_MODEL,
        temperature=0.1,
        openai_api_key=settings.OPENAI_API_KEY,
    )

# ── Shared LLM prompt ─────────────────────────────────────────────────────────
_INSIGHT_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """\
You are an operational intelligence analyst for a SaaS company.
Analyse the following data samples and identify the SINGLE most significant pattern or trend.

Return ONLY a JSON object (no markdown, no explanation) with this exact schema:
{{
  "title": "Short headline (< 10 words)",
  "summary": "1-2 sentence plain-English explanation of the insight",
  "magnitude": <number or null>,  // e.g. percentage change, count
  "insight_type": "<one of: complaint_spike | feature_trend | release_correlation | eng_bottleneck | churn_risk>"
}}

If there is no significant pattern, return: {{"insight_type": "none"}}
"""),
    ("human", "DATA:\n{data_summary}"),
])


async def _call_llm(data_summary: str) -> dict[str, Any] | None:
    chain = _INSIGHT_PROMPT | _llm
    result = await chain.ainvoke({"data_summary": data_summary})
    match = re.search(r"\{.*\}", result.content, re.DOTALL)
    if not match:
        return None
    parsed = json.loads(match.group())
    if parsed.get("insight_type") == "none":
        return None
    return parsed


def _magnitude_to_tier(value: Any) -> str:
    """
    Convert the LLM-returned magnitude (a raw number like 45 or a string)
    to one of the three tiers the DB and frontend expect: low / medium / high.
    """
    if isinstance(value, str) and value in ("low", "medium", "high"):
        return value
    try:
        n = float(value)
        if n >= 50:
            return "high"
        if n >= 20:
            return "medium"
        return "low"
    except (TypeError, ValueError):
        return "medium"


# ── Data Sufficiency Guard (Q14) ──────────────────────────────────────────────
# Minimum thresholds before any insight type activates.
# Below these thresholds the data is too sparse to produce reliable insights —
# false pattern detection early destroys trust in the feature permanently.

_THRESHOLD_PATTERN_INCIDENTS  = 3    # min incidents for pattern insights
_THRESHOLD_PATTERN_DAYS       = 14   # min days of history
_THRESHOLD_TREND_INCIDENTS     = 10   # min incidents for trend insights
_THRESHOLD_TREND_DAYS          = 30   # min days of history for trends
_THRESHOLD_DEPLOY_CORRELATION  = 5    # min deploys with incident co-occurrence


async def _check_data_sufficiency(tenant_id: str) -> dict:
    """
    Returns a dict describing whether each insight type has enough data to activate.

    Example return:
    {
      "pattern": {"ready": False, "incidents": 1, "days": 5,
                  "needs": "2 more incidents, 9 more days"},
      "trend":   {"ready": False, ...},
      "deploy":  {"ready": True, ...},
    }
    """
    from apps.api.db.models import Incident, CanonicalDocument as _CD

    now = datetime.now(tz=timezone.utc)
    cutoff_14 = now - timedelta(days=_THRESHOLD_PATTERN_DAYS)
    cutoff_30 = now - timedelta(days=_THRESHOLD_TREND_DAYS)

    async with AsyncSession() as db:
        # Count total incidents in last 30 days
        inc_result = await db.execute(
            sa.select(sa.func.count(Incident.id)).where(
                Incident.tenant_id == tenant_id,
                Incident.started_at >= cutoff_30,
            )
        )
        total_incidents_30d = inc_result.scalar_one() or 0

        # Count incidents in last 14 days
        inc_result_14 = await db.execute(
            sa.select(sa.func.count(Incident.id)).where(
                Incident.tenant_id == tenant_id,
                Incident.started_at >= cutoff_14,
            )
        )
        total_incidents_14d = inc_result_14.scalar_one() or 0

        # Earliest incident date (to compute days of history)
        first_inc_result = await db.execute(
            sa.select(sa.func.min(Incident.started_at)).where(
                Incident.tenant_id == tenant_id,
            )
        )
        first_incident_at = first_inc_result.scalar_one()
        days_of_history = (now - first_incident_at).days if first_incident_at else 0

        # Count GitHub deploys (PRs merged) in last 30 days
        deploy_result = await db.execute(
            sa.select(sa.func.count(_CD.id)).where(
                _CD.tenant_id == tenant_id,
                _CD.source_type == "github",
                _CD.source_created_at >= cutoff_30,
            )
        )
        total_deploys = deploy_result.scalar_one() or 0

    def _gap(current: int, needed: int) -> str:
        diff = needed - current
        return f"{diff} more" if diff > 0 else "met"

    pattern_ready = (total_incidents_14d >= _THRESHOLD_PATTERN_INCIDENTS
                     and days_of_history >= _THRESHOLD_PATTERN_DAYS)
    trend_ready   = (total_incidents_30d >= _THRESHOLD_TREND_INCIDENTS
                     and days_of_history >= _THRESHOLD_TREND_DAYS)
    deploy_ready  = total_deploys >= _THRESHOLD_DEPLOY_CORRELATION

    return {
        "pattern": {
            "ready":     pattern_ready,
            "incidents": total_incidents_14d,
            "days":      days_of_history,
            "needs": (
                "Ready" if pattern_ready else
                f"Need {_gap(total_incidents_14d, _THRESHOLD_PATTERN_INCIDENTS)} incidents "
                f"and {_gap(days_of_history, _THRESHOLD_PATTERN_DAYS)} days of history"
            ),
        },
        "trend": {
            "ready":     trend_ready,
            "incidents": total_incidents_30d,
            "days":      days_of_history,
            "needs": (
                "Ready" if trend_ready else
                f"Need {_gap(total_incidents_30d, _THRESHOLD_TREND_INCIDENTS)} incidents "
                f"and {_gap(days_of_history, _THRESHOLD_TREND_DAYS)} days of history"
            ),
        },
        "deploy": {
            "ready":   deploy_ready,
            "deploys": total_deploys,
            "needs": (
                "Ready" if deploy_ready else
                f"Need {_gap(total_deploys, _THRESHOLD_DEPLOY_CORRELATION)} more GitHub deploys"
            ),
        },
    }


async def _save_insight(db, tenant_id: str, parsed: dict, source_types: list[str]):
    insight = Insight(
        tenant_id=tenant_id,
        insight_type=parsed["insight_type"],
        title=parsed["title"],
        summary=parsed["summary"],
        magnitude=_magnitude_to_tier(parsed.get("magnitude")),
        source_types=source_types,
        evidence=parsed,          # DB column is "evidence", not "raw_data"
    )
    db.add(insight)
    await db.commit()
    logger.info("[%s] Saved insight: %s", tenant_id, parsed["title"])


# ── Complaint Spike Detector ─────────────────────────────────────────────────
@shared_task(name="insights.complaint_spike", bind=True, max_retries=2)
def detect_complaint_spike(self, tenant_id: str):
    try:
        _run_async(_detect_complaint_spike(tenant_id))
    except Exception as exc:
        raise self.retry(exc=exc)


async def _detect_complaint_spike(tenant_id: str):
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=7)

    async with AsyncSession() as db:
        result = await db.execute(
            sa.select(CanonicalDocument)
            .where(
                CanonicalDocument.tenant_id == tenant_id,
                CanonicalDocument.source_type.in_(["zendesk", "slack"]),
                CanonicalDocument.source_created_at >= cutoff,
                CanonicalDocument.embedding_status == "done",
            )
            .order_by(CanonicalDocument.source_created_at.desc())
            .limit(150)
        )
        docs = result.scalars().all()

        if len(docs) < 5:
            logger.info("[%s] Not enough data for complaint spike analysis", tenant_id)
            return

        data_summary = "\n".join(
            f"- [{d.source_type.upper()} | {d.source_created_at:%Y-%m-%d}] "
            f"{d.title}: {(d.content or '')[:250]}"
            for d in docs
        )
        parsed = await _call_llm(data_summary)
        if parsed:
            await _save_insight(db, tenant_id, parsed, ["zendesk", "slack"])


# ── Feature Request Trend Detector ──────────────────────────────────────────
@shared_task(name="insights.feature_trend", bind=True, max_retries=2)
def detect_feature_trend(self, tenant_id: str):
    try:
        _run_async(_detect_feature_trend(tenant_id))
    except Exception as exc:
        raise self.retry(exc=exc)


async def _detect_feature_trend(tenant_id: str):
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=30)

    async with AsyncSession() as db:
        result = await db.execute(
            sa.select(CanonicalDocument)
            .where(
                CanonicalDocument.tenant_id == tenant_id,
                CanonicalDocument.source_type.in_(["jira", "zendesk", "slack"]),
                CanonicalDocument.source_created_at >= cutoff,
                # Filter for likely feature-request documents
                sa.or_(
                    CanonicalDocument.title.ilike("%feature%"),
                    CanonicalDocument.title.ilike("%request%"),
                    CanonicalDocument.title.ilike("%enhancement%"),
                    sa.cast(CanonicalDocument.doc_metadata["labels"], sa.Text).ilike("%feature-request%"),
                ),
            )
            .order_by(CanonicalDocument.source_created_at.desc())
            .limit(100)
        )
        docs = result.scalars().all()

        if len(docs) < 3:
            return

        data_summary = "\n".join(
            f"- [{d.source_type.upper()}] {d.title}: {(d.content or '')[:200]}"
            for d in docs
        )
        parsed = await _call_llm(data_summary)
        if parsed:
            await _save_insight(db, tenant_id, parsed, ["jira", "zendesk", "slack"])


# ── Engineering Bottleneck Detector ─────────────────────────────────────────
@shared_task(name="insights.eng_bottleneck", bind=True, max_retries=2)
def detect_eng_bottleneck(self, tenant_id: str):
    try:
        _run_async(_detect_eng_bottleneck(tenant_id))
    except Exception as exc:
        raise self.retry(exc=exc)


async def _detect_eng_bottleneck(tenant_id: str):
    stale_cutoff = datetime.now(tz=timezone.utc) - timedelta(days=5)

    async with AsyncSession() as db:
        # Find Jira tickets in progress but not updated in > 5 days
        result = await db.execute(
            sa.select(CanonicalDocument)
            .where(
                CanonicalDocument.tenant_id == tenant_id,
                CanonicalDocument.source_type == "jira",
                sa.cast(CanonicalDocument.doc_metadata["status"], sa.Text).ilike("%in progress%"),
                CanonicalDocument.source_updated_at <= stale_cutoff,
            )
            .limit(50)
        )
        docs = result.scalars().all()

        if len(docs) < 2:
            return

        data_summary = (
            f"The following {len(docs)} Jira tickets have been In Progress for > 5 days "
            f"with no recent activity:\n"
        ) + "\n".join(
            f"- {d.title} (last updated: {d.source_updated_at:%Y-%m-%d})"
            for d in docs
        )
        parsed = await _call_llm(data_summary)
        if parsed:
            await _save_insight(db, tenant_id, parsed, ["jira"])


# ── Master scheduler ─────────────────────────────────────────────────────────
@shared_task(name="insights.run_all_for_tenant")
def run_all_insights_for_tenant(tenant_id: str):
    """
    Dispatches insight detectors for a tenant only if data sufficiency thresholds are met.

    Skips insight generation entirely when data is too sparse — a bad insight seen
    early permanently destroys trust in the feature. No insight is better than a
    wrong one. The threshold status is logged so the team can monitor activation rates.
    """
    sufficiency = _run_async(_check_data_sufficiency(tenant_id))

    any_ready = any(v.get("ready") for v in sufficiency.values())
    if not any_ready:
        logger.info(
            "[%s] Insights skipped — data thresholds not met. "
            "Pattern: %s | Trend: %s | Deploy: %s",
            tenant_id,
            sufficiency["pattern"]["needs"],
            sufficiency["trend"]["needs"],
            sufficiency["deploy"]["needs"],
        )
        return {"status": "insufficient_data", "thresholds": sufficiency}

    logger.info(
        "[%s] Insights running. Pattern ready=%s | Trend ready=%s | Deploy ready=%s",
        tenant_id,
        sufficiency["pattern"]["ready"],
        sufficiency["trend"]["ready"],
        sufficiency["deploy"]["ready"],
    )

    # Only dispatch detectors for which the threshold is met
    if sufficiency["pattern"]["ready"] or sufficiency["deploy"]["ready"]:
        detect_complaint_spike.delay(tenant_id)
    if sufficiency["trend"]["ready"]:
        detect_feature_trend.delay(tenant_id)
    if sufficiency["pattern"]["ready"]:
        detect_eng_bottleneck.delay(tenant_id)

    return {"status": "dispatched", "thresholds": sufficiency}
