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


async def _save_insight(db, tenant_id: str, parsed: dict, source_types: list[str]):
    insight = Insight(
        tenant_id=tenant_id,
        insight_type=parsed["insight_type"],
        title=parsed["title"],
        summary=parsed["summary"],
        magnitude=parsed.get("magnitude"),
        source_types=source_types,
        raw_data=parsed,
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
    """Dispatches all insight detectors for a single tenant."""
    detect_complaint_spike.delay(tenant_id)
    detect_feature_trend.delay(tenant_id)
    detect_eng_bottleneck.delay(tenant_id)
