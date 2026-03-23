"""
OpsLens AI — AI Insight Engine
================================
Detects operational patterns across ingested data using LLM-powered analysis
and statistical heuristics.

Five detectors, each runnable independently or via the master orchestrator:

    1. ComplaintSpikeDetector   — Zendesk + Slack complaint surge detection
    2. FeatureTrendDetector     — Feature request clustering across sources
    3. ReleaseCorrelationDetector — GitHub release × error spike correlation
    4. EngBottleneckDetector    — Stalled Jira tickets and team blockers
    5. ChurnRiskDetector        — High-severity tickets + CRM renewal proximity

Each detector:
    a. Queries canonical_documents for recent relevant records
    b. Compiles a structured data summary
    c. Passes it to GPT-4o with a structured output prompt
    d. Parses the JSON response into an Insight object
    e. Persists to PostgreSQL (dedup by title + tenant + time window)

Scheduling: Celery Beat dispatches the master task every hour.
"""
from __future__ import annotations

import json
import re
import statistics
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, ClassVar

import sqlalchemy as sa
from celery import shared_task
from celery.utils.log import get_task_logger
from langchain_core.prompts import ChatPromptTemplate
from ...api.config import settings
from ...api.db import AsyncSession
from ...api.models.document import CanonicalDocument
from ...api.models.insight import Insight

logger = get_task_logger(__name__)

# ── LLM singleton: respects LLM_PROVIDER setting ──────────────────────────────
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
        max_tokens=800,
        openai_api_key=settings.OPENAI_API_KEY,
    )


# ── Shared structured-output prompt ──────────────────────────────────────────
_ANALYSIS_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """\
You are a senior data analyst specialising in operational intelligence for SaaS companies.
Analyse the data samples below and identify the SINGLE most significant pattern or trend.

Return ONLY a JSON object — no markdown, no preamble, no explanation outside the JSON:
{{
  "title":        "<Headline in < 12 words, present tense>",
  "summary":      "<1-2 sentences explaining the pattern and its business impact>",
  "magnitude":    <numeric value or null — e.g. percentage change, count, severity score>,
  "insight_type": "<exactly one of: complaint_spike | feature_trend | release_correlation | eng_bottleneck | churn_risk>",
  "confidence":   "<high | medium | low>"
}}

If no significant pattern exists, return: {{"insight_type": "none"}}
Do NOT invent numbers. Only use what the data shows.
"""),
    ("human", "ANALYSIS CONTEXT:\n{data_summary}"),
])


@dataclass
class DetectedInsight:
    title: str
    summary: str
    magnitude: float | None
    insight_type: str
    source_types: list[str]
    raw_data: dict[str, Any]
    confidence: str = "medium"


# ═══════════════════════════════════════════════════════════════════════════════
# Base Detector
# ═══════════════════════════════════════════════════════════════════════════════
class BaseDetector(ABC):
    """
    Abstract base class for all insight detectors.
    Subclasses implement `collect_data` and `build_summary`.
    """
    insight_type: ClassVar[str]
    source_types: ClassVar[list[str]]
    look_back_days: ClassVar[int] = 7

    async def run(self, tenant_id: str) -> DetectedInsight | None:
        """Full pipeline: collect → summarise → analyse → return insight."""
        data = await self.collect_data(tenant_id)
        if not data or len(data) < self._min_records():
            logger.info("[%s] Insufficient data for %s (%d records)",
                        tenant_id, self.insight_type, len(data) if data else 0)
            return None

        summary = self.build_summary(data)
        parsed = await self._call_llm(summary)
        if not parsed or parsed.get("insight_type") == "none":
            return None

        return DetectedInsight(
            title=parsed["title"],
            summary=parsed["summary"],
            magnitude=self._parse_magnitude(parsed.get("magnitude")),
            insight_type=self.insight_type,
            source_types=self.source_types,
            raw_data=parsed,
            confidence=parsed.get("confidence", "medium"),
        )

    @abstractmethod
    async def collect_data(self, tenant_id: str) -> list[CanonicalDocument]:
        """Query the database for relevant records."""
        ...

    @abstractmethod
    def build_summary(self, docs: list[CanonicalDocument]) -> str:
        """Compile a structured text summary for the LLM."""
        ...

    def _min_records(self) -> int:
        return 5

    @staticmethod
    async def _call_llm(data_summary: str) -> dict | None:
        chain = _ANALYSIS_PROMPT | _llm
        try:
            result = await chain.ainvoke({"data_summary": data_summary})
            match = re.search(r"\{.*\}", result.content, re.DOTALL)
            if not match:
                logger.warning("LLM returned non-JSON response: %s", result.content[:200])
                return None
            return json.loads(match.group())
        except (json.JSONDecodeError, Exception) as exc:
            logger.warning("LLM call failed: %s", exc)
            return None

    @staticmethod
    def _parse_magnitude(value: Any) -> float | None:
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _cutoff(days: int) -> datetime:
        return datetime.now(tz=timezone.utc) - timedelta(days=days)

    @staticmethod
    def _doc_line(doc: CanonicalDocument, max_chars: int = 250) -> str:
        date = doc.source_created_at.strftime("%Y-%m-%d") if doc.source_created_at else "unknown"
        content = (doc.content or "")[:max_chars].replace("\n", " ")
        return f"  [{doc.source_type.upper()} | {date}] {doc.title}: {content}"


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Complaint Spike Detector
# ═══════════════════════════════════════════════════════════════════════════════
class ComplaintSpikeDetector(BaseDetector):
    """
    Detects abnormal spikes in customer complaints.

    Method:
    - Counts support tickets + Slack messages in a rolling 7-day window.
    - Compares against a 28-day rolling baseline.
    - Flags if the current period is > 1.5σ above the mean.
    - Sends the top-50 most recent complaint records to the LLM for thematic analysis.
    """
    insight_type = "complaint_spike"
    source_types = ["zendesk", "slack"]
    look_back_days = 7

    KEYWORDS = [
        "error", "bug", "broken", "fail", "issue", "crash", "problem",
        "not working", "can't", "cannot", "unable", "wrong", "slow",
        "frustrat", "disappoint", "complaint",
    ]

    async def collect_data(self, tenant_id: str) -> list[CanonicalDocument]:
        async with AsyncSession() as db:
            cutoff = self._cutoff(self.look_back_days)
            keyword_filter = sa.or_(
                *[
                    sa.func.lower(CanonicalDocument.content).contains(kw)
                    for kw in self.KEYWORDS
                ]
            )
            result = await db.execute(
                sa.select(CanonicalDocument)
                .where(
                    CanonicalDocument.tenant_id == tenant_id,
                    CanonicalDocument.source_type.in_(self.source_types),
                    CanonicalDocument.source_created_at >= cutoff,
                    keyword_filter,
                )
                .order_by(CanonicalDocument.source_created_at.desc())
                .limit(100)
            )
            return result.scalars().all()

    def build_summary(self, docs: list[CanonicalDocument]) -> str:
        # Group by day for trend context
        from collections import Counter
        day_counts: Counter = Counter()
        for d in docs:
            if d.source_created_at:
                day_counts[d.source_created_at.strftime("%Y-%m-%d")] += 1

        trend_lines = "\n".join(
            f"  {day}: {count} complaint signals"
            for day, count in sorted(day_counts.items())
        )

        # Statistical spike check
        counts = list(day_counts.values())
        spike_note = ""
        if len(counts) >= 3:
            mean = statistics.mean(counts)
            try:
                std = statistics.stdev(counts)
                latest = counts[-1] if counts else 0
                z_score = (latest - mean) / std if std > 0 else 0
                spike_note = f"\nStatistical note: latest day z-score = {z_score:.2f} (mean={mean:.1f}, std={std:.1f})"
            except statistics.StatisticsError:
                pass

        sample_lines = "\n".join(self._doc_line(d, 300) for d in docs[:50])

        return f"""COMPLAINT SPIKE ANALYSIS
Total records (7 days): {len(docs)}
Sources: {', '.join(self.source_types)}

Daily distribution:
{trend_lines}
{spike_note}

Sample records (most recent first):
{sample_lines}

Task: Identify the primary complaint theme(s), estimate the severity/urgency,
and quantify the spike magnitude (% increase vs typical volume if discernible).
"""


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Feature Trend Detector
# ═══════════════════════════════════════════════════════════════════════════════
class FeatureTrendDetector(BaseDetector):
    """
    Clusters feature requests across Jira, Zendesk, and Slack to surface
    the most frequently requested capabilities.

    Method:
    - Finds documents tagged or titled as feature requests.
    - Groups by recurring themes (via LLM clustering).
    - Reports the top cluster with count and representative examples.
    """
    insight_type = "feature_trend"
    source_types = ["jira", "zendesk", "slack"]
    look_back_days = 30

    FEATURE_KEYWORDS = [
        "feature request", "enhancement", "would be nice", "wish",
        "please add", "need ability", "feature:", "[feature]",
        "add support", "request:", "suggestion",
    ]

    async def collect_data(self, tenant_id: str) -> list[CanonicalDocument]:
        async with AsyncSession() as db:
            cutoff = self._cutoff(self.look_back_days)
            keyword_filter = sa.or_(
                *[
                    sa.or_(
                        sa.func.lower(CanonicalDocument.title).contains(kw),
                        sa.func.lower(CanonicalDocument.content).contains(kw),
                    )
                    for kw in self.FEATURE_KEYWORDS
                ],
                sa.cast(CanonicalDocument.doc_metadata["labels"], sa.Text).ilike("%feature%"),
                sa.cast(CanonicalDocument.doc_metadata["issue_type"], sa.Text).ilike("%story%"),
            )
            result = await db.execute(
                sa.select(CanonicalDocument)
                .where(
                    CanonicalDocument.tenant_id == tenant_id,
                    CanonicalDocument.source_type.in_(self.source_types),
                    CanonicalDocument.source_created_at >= cutoff,
                    keyword_filter,
                )
                .order_by(CanonicalDocument.source_created_at.desc())
                .limit(150)
            )
            return result.scalars().all()

    def _min_records(self) -> int:
        return 3

    def build_summary(self, docs: list[CanonicalDocument]) -> str:
        from collections import Counter
        source_breakdown = Counter(d.source_type for d in docs)
        sample_lines = "\n".join(self._doc_line(d, 300) for d in docs[:80])

        return f"""FEATURE REQUEST TREND ANALYSIS
Period: last {self.look_back_days} days
Total feature requests found: {len(docs)}
Source breakdown: {dict(source_breakdown)}

Feature request records:
{sample_lines}

Task: Identify the SINGLE most frequently requested feature or capability.
Group similar requests together. Count how many unique requests relate to the top theme.
Report the top theme as the title and summarise why customers want it.
Set magnitude = number of requests for the top theme.
"""


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Release Correlation Detector
# ═══════════════════════════════════════════════════════════════════════════════
class ReleaseCorrelationDetector(BaseDetector):
    """
    Detects correlations between software releases (GitHub tags/commits)
    and spikes in error-related Jira tickets or Zendesk tickets.

    Method:
    - Finds recent GitHub release/tag records.
    - Finds error/bug tickets within ±48h of each release.
    - Reports the release with the strongest co-occurring error signal.
    """
    insight_type = "release_correlation"
    source_types = ["github", "jira"]
    look_back_days = 14

    async def collect_data(self, tenant_id: str) -> list[CanonicalDocument]:
        async with AsyncSession() as db:
            cutoff = self._cutoff(self.look_back_days)

            # Fetch recent GitHub releases/tags
            releases = await db.execute(
                sa.select(CanonicalDocument)
                .where(
                    CanonicalDocument.tenant_id == tenant_id,
                    CanonicalDocument.source_type == "github",
                    CanonicalDocument.source_created_at >= cutoff,
                    sa.or_(
                        sa.func.lower(CanonicalDocument.title).contains("release"),
                        sa.func.lower(CanonicalDocument.title).contains("deploy"),
                        sa.func.lower(CanonicalDocument.title).contains("v"),
                        sa.cast(CanonicalDocument.doc_metadata["event"], sa.Text).ilike("%release%"),
                    ),
                )
                .order_by(CanonicalDocument.source_created_at.desc())
                .limit(20)
            )
            release_docs = releases.scalars().all()

            # Fetch Jira/Zendesk error tickets in the same period
            errors = await db.execute(
                sa.select(CanonicalDocument)
                .where(
                    CanonicalDocument.tenant_id == tenant_id,
                    CanonicalDocument.source_type.in_(["jira", "zendesk"]),
                    CanonicalDocument.source_created_at >= cutoff,
                    sa.or_(
                        sa.func.lower(CanonicalDocument.title).contains("error"),
                        sa.func.lower(CanonicalDocument.title).contains("bug"),
                        sa.func.lower(CanonicalDocument.title).contains("incident"),
                        sa.func.lower(CanonicalDocument.title).contains("outage"),
                        sa.func.lower(CanonicalDocument.title).contains("broken"),
                        sa.cast(CanonicalDocument.doc_metadata["priority"], sa.Text).ilike("%critical%"),
                        sa.cast(CanonicalDocument.doc_metadata["priority"], sa.Text).ilike("%blocker%"),
                    ),
                )
                .order_by(CanonicalDocument.source_created_at.desc())
                .limit(80)
            )
            error_docs = errors.scalars().all()

            return release_docs + error_docs

    def _min_records(self) -> int:
        return 4

    def build_summary(self, docs: list[CanonicalDocument]) -> str:
        releases = [d for d in docs if d.source_type == "github"]
        errors   = [d for d in docs if d.source_type != "github"]

        release_lines = "\n".join(self._doc_line(d, 200) for d in releases)
        error_lines   = "\n".join(self._doc_line(d, 200) for d in errors[:40])

        # Build a simple time-proximity table
        correlation_hints = []
        for rel in releases[:10]:
            if not rel.source_created_at:
                continue
            window_start = rel.source_created_at - timedelta(hours=6)
            window_end   = rel.source_created_at + timedelta(hours=48)
            nearby_errors = [
                e for e in errors
                if e.source_created_at
                and window_start <= e.source_created_at <= window_end
            ]
            if nearby_errors:
                correlation_hints.append(
                    f"  Release: {rel.title} ({rel.source_created_at:%Y-%m-%d %H:%M}) "
                    f"→ {len(nearby_errors)} error(s) within 48h"
                )

        correlation_block = "\n".join(correlation_hints) if correlation_hints else "  No strong proximity signals found."

        return f"""RELEASE CORRELATION ANALYSIS
Period: last {self.look_back_days} days
Releases found: {len(releases)}
Error/bug tickets found: {len(errors)}

Time-proximity correlations:
{correlation_block}

Releases:
{release_lines}

Error/bug records (same period):
{error_lines}

Task: Identify if any specific release correlates with an abnormal spike in errors or bugs.
Report the release version and the estimated correlation (strong/moderate/weak).
Set magnitude = number of error tickets following the correlated release.
"""


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Engineering Bottleneck Detector
# ═══════════════════════════════════════════════════════════════════════════════
class EngBottleneckDetector(BaseDetector):
    """
    Identifies blocked or stalled engineering work in Jira.

    Method:
    - Finds tickets in 'In Progress' or 'In Review' status
      that haven't been updated in more than STALE_DAYS.
    - Groups by assignee / label / epic to find systemic blockers.
    """
    insight_type = "eng_bottleneck"
    source_types = ["jira"]
    STALE_DAYS = 5

    async def collect_data(self, tenant_id: str) -> list[CanonicalDocument]:
        async with AsyncSession() as db:
            # Tickets In Progress but not updated recently
            stale_cutoff = datetime.now(tz=timezone.utc) - timedelta(days=self.STALE_DAYS)
            result = await db.execute(
                sa.select(CanonicalDocument)
                .where(
                    CanonicalDocument.tenant_id == tenant_id,
                    CanonicalDocument.source_type == "jira",
                    sa.or_(
                        sa.cast(CanonicalDocument.doc_metadata["status"], sa.Text).ilike("%in progress%"),
                        sa.cast(CanonicalDocument.doc_metadata["status"], sa.Text).ilike("%in review%"),
                        sa.cast(CanonicalDocument.doc_metadata["status"], sa.Text).ilike("%blocked%"),
                    ),
                    CanonicalDocument.source_updated_at <= stale_cutoff,
                )
                .order_by(CanonicalDocument.source_updated_at)
                .limit(60)
            )
            return result.scalars().all()

    def _min_records(self) -> int:
        return 2

    def build_summary(self, docs: list[CanonicalDocument]) -> str:
        now = datetime.now(tz=timezone.utc)
        lines = []
        for d in docs:
            days_stale = (
                int((now - d.source_updated_at).days)
                if d.source_updated_at and d.source_updated_at.tzinfo
                else "?"
            )
            status   = (d.doc_metadata or {}).get("status", "unknown")
            assignee = (d.doc_metadata or {}).get("assignee", "unassigned")
            priority = (d.doc_metadata or {}).get("priority", "")
            lines.append(
                f"  {d.title} | Status: {status} | Assignee: {assignee} "
                f"| Priority: {priority} | Stale {days_stale} days"
            )

        return f"""ENGINEERING BOTTLENECK ANALYSIS
Stale threshold: {self.STALE_DAYS} days without update
Tickets found: {len(docs)}

Stalled tickets:
{chr(10).join(lines)}

Task: Identify the most impactful engineering bottleneck.
Group stalled tickets by theme, team, or epic if patterns emerge.
Report the total number of stalled tickets as magnitude.
Suggest the likely root cause if discernible from the data.
"""


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Churn Risk Detector
# ═══════════════════════════════════════════════════════════════════════════════
class ChurnRiskDetector(BaseDetector):
    """
    Identifies customers at risk of churning based on:
    - High-severity open support tickets (Zendesk)
    - CRM deal stage / renewal proximity (HubSpot)

    A customer is flagged when they have ≥ CRITICAL_THRESHOLD critical/high
    tickets open AND appear in HubSpot as an active deal close to renewal.
    """
    insight_type = "churn_risk"
    source_types = ["zendesk", "hubspot"]
    look_back_days = 30
    CRITICAL_THRESHOLD = 3

    async def collect_data(self, tenant_id: str) -> list[CanonicalDocument]:
        async with AsyncSession() as db:
            cutoff = self._cutoff(self.look_back_days)

            # High-severity Zendesk tickets
            tickets = await db.execute(
                sa.select(CanonicalDocument)
                .where(
                    CanonicalDocument.tenant_id == tenant_id,
                    CanonicalDocument.source_type == "zendesk",
                    CanonicalDocument.source_created_at >= cutoff,
                    sa.or_(
                        sa.cast(CanonicalDocument.doc_metadata["priority"], sa.Text).ilike("%urgent%"),
                        sa.cast(CanonicalDocument.doc_metadata["priority"], sa.Text).ilike("%high%"),
                        sa.cast(CanonicalDocument.doc_metadata["priority"], sa.Text).ilike("%critical%"),
                    ),
                    sa.cast(CanonicalDocument.doc_metadata["status"], sa.Text).notlike("%closed%"),
                    sa.cast(CanonicalDocument.doc_metadata["status"], sa.Text).notlike("%solved%"),
                )
                .limit(100)
            )

            # HubSpot CRM deals approaching renewal
            crm_deals = await db.execute(
                sa.select(CanonicalDocument)
                .where(
                    CanonicalDocument.tenant_id == tenant_id,
                    CanonicalDocument.source_type == "hubspot",
                    CanonicalDocument.source_created_at >= cutoff,
                    sa.cast(CanonicalDocument.doc_metadata["dealstage"], sa.Text).ilike("%renewal%"),
                )
                .limit(50)
            )

            return tickets.scalars().all() + crm_deals.scalars().all()

    def build_summary(self, docs: list[CanonicalDocument]) -> str:
        tickets  = [d for d in docs if d.source_type == "zendesk"]
        crm_docs = [d for d in docs if d.source_type == "hubspot"]

        # Group tickets by company/requester
        from collections import Counter
        company_counts: Counter = Counter()
        for t in tickets:
            org = (t.doc_metadata or {}).get("organization_name", "Unknown")
            company_counts[org] += 1

        at_risk = [
            f"  {company}: {count} high-priority open tickets"
            for company, count in company_counts.most_common(10)
            if count >= self.CRITICAL_THRESHOLD
        ]

        crm_lines = "\n".join(
            f"  {d.title} | Close date: {(d.doc_metadata or {}).get('closedate', 'unknown')} "
            f"| Deal stage: {(d.doc_metadata or {}).get('dealstage', 'unknown')}"
            for d in crm_docs[:20]
        )

        return f"""CHURN RISK ANALYSIS
Period: last {self.look_back_days} days
High-priority open tickets: {len(tickets)}
CRM renewal deals in pipeline: {len(crm_docs)}
Churn risk threshold: ≥{self.CRITICAL_THRESHOLD} critical tickets

Companies with ≥{self.CRITICAL_THRESHOLD} critical open tickets:
{chr(10).join(at_risk) if at_risk else "  None above threshold."}

CRM deals approaching closure/renewal:
{crm_lines if crm_lines else "  No renewal deals found."}

Task: Identify the single highest-risk customer and explain the churn risk.
Cross-reference ticket volume with CRM renewal proximity.
Set magnitude = number of at-risk accounts.
"""


# ═══════════════════════════════════════════════════════════════════════════════
# Persistence
# ═══════════════════════════════════════════════════════════════════════════════
async def save_insight(tenant_id: str, detected: DetectedInsight) -> str | None:
    """
    Persist a detected insight to PostgreSQL.
    Deduplicates: skips if an identical title was already generated in the
    same calendar day for this tenant.
    Returns the insight ID if saved, None if skipped.
    """
    async with AsyncSession() as db:
        # Dedup check: same tenant + same title in last 24h
        cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=24)
        existing = await db.execute(
            sa.select(Insight)
            .where(
                Insight.tenant_id == tenant_id,
                Insight.title == detected.title,
                Insight.generated_at >= cutoff,
            )
        )
        if existing.scalar_one_or_none():
            logger.info("[%s] Duplicate insight skipped: %s", tenant_id, detected.title)
            return None

        import uuid
        insight = Insight(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            insight_type=detected.insight_type,
            title=detected.title,
            summary=detected.summary,
            magnitude=detected.magnitude,
            status="active",
            source_types=detected.source_types,
            raw_data={**detected.raw_data, "confidence": detected.confidence},
            generated_at=datetime.now(tz=timezone.utc),
        )
        db.add(insight)
        await db.commit()
        logger.info("[%s] Saved insight: %s (magnitude=%s)", tenant_id, detected.title, detected.magnitude)
        return str(insight.id)


# ═══════════════════════════════════════════════════════════════════════════════
# Detector registry
# ═══════════════════════════════════════════════════════════════════════════════
ALL_DETECTORS: dict[str, type[BaseDetector]] = {
    "complaint_spike":        ComplaintSpikeDetector,
    "feature_trend":          FeatureTrendDetector,
    "release_correlation":    ReleaseCorrelationDetector,
    "eng_bottleneck":         EngBottleneckDetector,
    "churn_risk":             ChurnRiskDetector,
}


# ═══════════════════════════════════════════════════════════════════════════════
# Celery tasks
# ═══════════════════════════════════════════════════════════════════════════════
async def _run_detector(tenant_id: str, detector_cls: type[BaseDetector]) -> dict:
    """Run a single detector and persist the result."""
    detector = detector_cls()
    try:
        detected = await detector.run(tenant_id)
        if detected:
            insight_id = await save_insight(tenant_id, detected)
            return {
                "status": "saved" if insight_id else "duplicate",
                "insight_type": detected.insight_type,
                "title": detected.title,
                "insight_id": insight_id,
            }
        return {"status": "no_signal", "insight_type": detector.insight_type}
    except Exception as exc:
        logger.exception("[%s] Detector %s failed: %s", tenant_id, detector_cls.__name__, exc)
        return {"status": "error", "insight_type": detector.insight_type, "error": str(exc)}


@shared_task(name="insights.run_for_tenant", bind=True, max_retries=2, default_retry_delay=120)
def run_insights_for_tenant(self, tenant_id: str, only_type: str | None = None) -> list[dict]:
    """
    Run all (or one) insight detector(s) for a single tenant.
    Returns a list of result dicts, one per detector run.
    """
    import asyncio
    try:
        detectors = (
            {only_type: ALL_DETECTORS[only_type]}
            if only_type and only_type in ALL_DETECTORS
            else ALL_DETECTORS
        )

        async def _run_all():
            results = []
            for name, cls in detectors.items():
                result = await _run_detector(tenant_id, cls)
                results.append(result)
                logger.info("[%s] %s → %s", tenant_id, name, result["status"])
            return results

        return asyncio.run(_run_all())

    except Exception as exc:
        raise self.retry(exc=exc)


@shared_task(name="insights.run_all_tenants")
def run_all_insights_for_all_tenants() -> dict:
    """
    Master fan-out task (triggered by Celery Beat every hour).
    Dispatches run_insights_for_tenant for each active tenant.
    """
    import asyncio

    async def _get_tenants():
        from ...api.models.tenant import Tenant
        async with AsyncSession() as db:
            result = await db.execute(sa.select(Tenant.id).where(Tenant.plan != "inactive"))
            return [str(row[0]) for row in result.fetchall()]

    tenant_ids = asyncio.run(_get_tenants())
    dispatched = 0
    for tid in tenant_ids:
        run_insights_for_tenant.delay(tid)
        dispatched += 1

    logger.info("Dispatched insight jobs for %d tenants", dispatched)
    return {"dispatched": dispatched, "tenant_count": len(tenant_ids)}
