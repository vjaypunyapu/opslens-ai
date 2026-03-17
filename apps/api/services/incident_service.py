"""
OpsLens AI — Incident Investigation Service
=============================================
Correlates signals across Qdrant (all source types) and generates a structured
root cause analysis using GPT-4o.

The investigation:
  1. Searches Qdrant for documents related to the incident title/service
     across all source types (logs, github, jira, slack, etc.)
  2. Separates signals by type and sorts them by timestamp
  3. Builds a timeline of events
  4. Prompts GPT-4o to produce root cause, contributing factors, recommendations
  5. Persists results back to the Incident row
"""
from __future__ import annotations

import json

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from ..config import settings
from ..db.models import Incident
from ..db.session import AsyncSessionFactory
from ..utils.logging import get_logger

logger = get_logger(__name__)

_embeddings = OpenAIEmbeddings(
    model="text-embedding-3-small",
    api_key=settings.OPENAI_API_KEY,
)
_llm = ChatOpenAI(model="gpt-4o", temperature=0.1, api_key=settings.OPENAI_API_KEY)

_RCA_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """\
You are an expert Site Reliability Engineer (SRE) conducting a post-incident root cause analysis.

You will be given:
- INCIDENT: title, description, and affected service
- SIGNALS: a timeline of correlated events from logs, code commits, Jira tickets, and Slack messages

Your job is to:
1. Identify the most likely ROOT CAUSE
2. List CONTRIBUTING FACTORS (up to 5)
3. Provide RECOMMENDATIONS to prevent recurrence (up to 5)
4. Produce a NARRATIVE: a clear 3-5 sentence explanation suitable for an incident report

Respond ONLY with a valid JSON object:
{{
  "root_cause": "...",
  "contributing_factors": ["...", "..."],
  "recommendations": ["...", "..."],
  "narrative": "..."
}}

Be specific. Reference actual signal details (commit SHAs, ticket IDs, timestamps) where possible.
If signals are insufficient, say so clearly in root_cause.
"""),
    ("human", """\
INCIDENT
--------
Title:       {title}
Service:     {service}
Started At:  {started_at}
Description: {description}

CORRELATED SIGNALS ({signal_count} events, sorted by time)
------------------
{signals}
"""),
])


async def investigate_incident(
    incident_id: str,
    tenant_id: str,
    company_name: str,
) -> None:
    """
    Full investigation pipeline.  Updates the Incident row in-place.
    Designed to run as a background task (called from the router or Celery).
    """
    from qdrant_client import QdrantClient

    qdrant = QdrantClient(url=settings.QDRANT_URL, api_key=settings.QDRANT_API_KEY or None)
    collection = f"opslens_{tenant_id}"

    async with AsyncSessionFactory() as db:
        import sqlalchemy as sa
        result = await db.execute(sa.select(Incident).where(Incident.id == incident_id))
        incident = result.scalar_one_or_none()
        if not incident:
            logger.warning("Incident %s not found", incident_id)
            return

        incident.status = "analysing"
        await db.commit()

        try:
            # ── 1. Embed the incident title for semantic search ────────────────
            query_text = f"{incident.title} {incident.service or ''} {incident.description or ''}".strip()
            query_vec = await _embeddings.aembed_query(query_text)

            # ── 2. Search Qdrant across all source types ───────────────────────
            collections_exist = {c.name for c in qdrant.get_collections().collections}
            signals: list[dict] = []

            if collection in collections_exist:
                hits = qdrant.search(
                    collection_name=collection,
                    query_vector=query_vec,
                    limit=40,
                    score_threshold=0.35,
                )
                for hit in hits:
                    p = hit.payload or {}
                    signals.append({
                        "source_type":    p.get("source_type", "unknown"),
                        "title":          p.get("title", ""),
                        "detail":         p.get("content_preview", ""),
                        "url":            p.get("url", ""),
                        "timestamp":      p.get("created_at", ""),
                        "author":         p.get("author", ""),
                        "score":          round(hit.score, 3),
                    })

            # ── 3. Sort by timestamp ───────────────────────────────────────────
            def _ts(s: dict) -> str:
                return s.get("timestamp") or "1970-01-01T00:00:00+00:00"

            signals.sort(key=_ts)

            # ── 4. Build timeline ──────────────────────────────────────────────
            timeline = []
            for s in signals:
                source_type = s["source_type"]
                event_type = _classify_event(source_type, s["title"])
                timeline.append({
                    "timestamp":  s["timestamp"],
                    "source":     source_type,
                    "source_type": source_type,
                    "event_type": event_type,
                    "title":      s["title"],
                    "detail":     s["detail"],
                    "url":        s["url"],
                    "author":     s["author"],
                })

            # ── 5. Generate RCA via LLM ────────────────────────────────────────
            signals_text = "\n\n".join(
                f"[{i+1}] {s['timestamp'][:19]} | {s['source_type'].upper()} | "
                f"{s['title']}\n     {s['detail'][:300]}"
                for i, s in enumerate(signals[:30])  # cap context
            ) or "No correlated signals found across connected data sources."

            chain = _RCA_PROMPT | _llm
            raw = await chain.ainvoke({
                "title":        incident.title,
                "service":      incident.service or "unknown",
                "started_at":   incident.started_at.isoformat() if incident.started_at else "unknown",
                "description":  incident.description or "No description provided.",
                "signal_count": len(signals),
                "signals":      signals_text,
            })

            # ── 6. Parse LLM output ────────────────────────────────────────────
            content = raw.content if hasattr(raw, "content") else str(raw)
            # Strip markdown code fences if present
            clean = content.strip()
            if clean.startswith("```"):
                clean = "\n".join(clean.split("\n")[1:])
            if clean.endswith("```"):
                clean = "\n".join(clean.split("\n")[:-1])

            try:
                rca = json.loads(clean)
            except json.JSONDecodeError:
                rca = {
                    "root_cause": content[:1000],
                    "contributing_factors": [],
                    "recommendations": [],
                    "narrative": content[:500],
                }

            # ── 7. Persist ────────────────────────────────────────────────────
            incident.timeline = timeline
            incident.signals  = signals
            incident.root_cause = rca.get("root_cause") or rca.get("narrative", "")
            incident.contributing_factors = rca.get("contributing_factors", [])[:5]
            incident.recommendations      = rca.get("recommendations", [])[:5]
            incident.status = "analysing"  # keep as analysing until human confirms

            await db.commit()
            logger.info(
                "Investigation complete for incident %s: %d signals, root_cause set",
                incident_id, len(signals),
            )

        except Exception as exc:
            logger.exception("Investigation failed for %s: %s", incident_id, exc)
            incident.status = "investigating"  # roll back so user can retry
            incident.root_cause = f"Investigation failed: {exc}"
            await db.commit()


def _classify_event(source_type: str, title: str) -> str:
    """Map source + title keywords to a human-readable event type."""
    t = title.lower()
    mapping = {
        "github":   "deploy" if any(k in t for k in ("deploy", "release", "merge", "tag")) else "code_change",
        "jira":     "incident" if any(k in t for k in ("incident", "outage", "p0", "p1")) else "ticket",
        "slack":    "alert" if any(k in t for k in ("alert", "pager", "down", "error")) else "discussion",
        "log":      "error" if any(k in t for k in ("error", "exception", "fatal", "critical")) else "log_event",
        "datadog":  "alert" if "alert" in t else "metric_anomaly",
        "cloudwatch": "alarm" if "alarm" in t else "log_event",
        "elasticsearch": "error" if "error" in t else "log_event",
        "splunk":   "error" if "error" in t else "log_event",
    }
    return mapping.get(source_type, "event")
