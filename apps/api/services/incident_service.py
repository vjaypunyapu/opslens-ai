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
from datetime import datetime, timezone

from langchain_core.prompts import ChatPromptTemplate

from ..config import settings
from ..db.models import Incident
from ..db.session import AsyncSessionFactory
from ..utils.logging import get_logger

logger = get_logger(__name__)

# ── LLM + embeddings: respects LLM_PROVIDER setting ──────────────────────────
if settings.LLM_PROVIDER == "ollama":
    from langchain_openai import ChatOpenAI, OpenAIEmbeddings
    _llm = ChatOpenAI(
        model=settings.OLLAMA_CHAT_MODEL,
        base_url=f"{settings.OLLAMA_URL}/v1",
        api_key="ollama",
        temperature=0.1,
        timeout=300,
    )
    _embeddings = OpenAIEmbeddings(
        model=settings.OLLAMA_EMBED_MODEL,
        base_url=f"{settings.OLLAMA_URL}/v1",
        api_key="ollama",
        check_embedding_ctx_length=False,
    )
elif settings.LLM_PROVIDER == "claude":
    from langchain_anthropic import ChatAnthropic
    from langchain_openai import OpenAIEmbeddings
    _llm = ChatAnthropic(
        model=settings.ANTHROPIC_CHAT_MODEL,
        anthropic_api_key=settings.ANTHROPIC_API_KEY,
        temperature=0.1,
    )
    _embeddings = OpenAIEmbeddings(
        model=settings.OPENAI_EMBED_MODEL,
        api_key=settings.OPENAI_API_KEY,
    )
else:
    from langchain_openai import ChatOpenAI, OpenAIEmbeddings
    _llm = ChatOpenAI(model=settings.OPENAI_CHAT_MODEL, temperature=0.1, api_key=settings.OPENAI_API_KEY)
    _embeddings = OpenAIEmbeddings(model=settings.OPENAI_EMBED_MODEL, api_key=settings.OPENAI_API_KEY)

_RCA_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """\
You are an expert Site Reliability Engineer (SRE) performing a root cause analysis.

Rules:
- ALWAYS produce a specific, actionable root_cause — never say "insufficient signals".
- If correlated signals are sparse, base your analysis on the incident title, error type, and service name.
- Use your SRE knowledge: a NullPointerException means an unhandled null reference; a ConnectionPool error means resource exhaustion; a TimeoutError means latency or deadlock, etc.
- Reference specific signal details (commit SHAs, ticket IDs, timestamps) when available.
- Recommendations must be concrete, numbered steps — not generic advice.

Respond ONLY with valid JSON:
{{
  "root_cause": "One clear sentence identifying the most likely cause.",
  "contributing_factors": ["factor 1", "factor 2", "factor 3"],
  "recommendations": [
    "Step 1: ...",
    "Step 2: ...",
    "Step 3: ..."
  ],
  "narrative": "3-5 sentence summary suitable for an incident report."
}}
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

Produce a complete root cause analysis. Even if signals are limited, use the error type and service context to give specific, actionable findings.
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
    import os as _os
    import urllib.parse as _urlparse
    from qdrant_client import QdrantClient
    from qdrant_client.models import Filter, FieldCondition, MatchValue

    _q = _urlparse.urlparse(settings.QDRANT_URL)
    _qkey = (
        _os.environ.get("QDRANT_TOKEN", "")
        or _os.environ.get("QDRANT_API_KEY", "")
        or (settings.QDRANT_API_KEY or "")
    ).strip() or None
    qdrant = QdrantClient(
        host=_q.hostname,
        port=_q.port or 6333,
        https=(_q.scheme == "https"),
        api_key=_qkey,
        prefer_grpc=False,
    )
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
                # qdrant-client >= 2.0 replaced .search() with .query_points()
                try:
                    response = qdrant.query_points(
                        collection_name=collection,
                        query=query_vec,
                        limit=40,
                        score_threshold=0.35,
                    )
                    raw_hits = response.points
                except AttributeError:
                    # fallback for older qdrant-client 1.x
                    raw_hits = qdrant.search(
                        collection_name=collection,
                        query_vector=query_vec,
                        limit=40,
                        score_threshold=0.35,
                    )
                for hit in raw_hits:
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
            incident.status = "investigating"  # ready for human review

            await db.commit()
            logger.info(
                "Investigation complete for incident %s: %d signals, root_cause set",
                incident_id, len(signals),
            )

            # ── 8. Slack notification ─────────────────────────────────────────
            try:
                import sqlalchemy as _sa
                from ..models.log_ops import AlertRoutingRule
                webhook_result = await db.execute(
                    _sa.select(AlertRoutingRule.slack_webhook)
                    .where(
                        AlertRoutingRule.tenant_id == tenant_id,
                        AlertRoutingRule.slack_webhook.isnot(None),
                    )
                    .order_by(AlertRoutingRule.priority)
                    .limit(1)
                )
                webhook_url = webhook_result.scalar_one_or_none()

                if webhook_url:
                    import httpx as _httpx
                    root_cause = incident.root_cause or "See investigation signals for details."
                    recommendations = incident.recommendations or []
                    rec_text = "\n".join(
                        f"• {r}" for r in recommendations[:3]
                    ) or "Review the investigation panel in OpsLens for next steps."

                    payload = {
                        "text": f"🔍 Investigation Complete — {incident.title}",
                        "blocks": [
                            {
                                "type": "header",
                                "text": {"type": "plain_text", "text": "🔍 Investigation Complete"},
                            },
                            {
                                "type": "section",
                                "text": {
                                    "type": "mrkdwn",
                                    "text": f"*{incident.title}*\n_{incident.service or 'Unknown service'}_ · {len(signals)} correlated signals",
                                },
                            },
                            {"type": "divider"},
                            {
                                "type": "section",
                                "text": {
                                    "type": "mrkdwn",
                                    "text": f"*Root Cause*\n{root_cause[:500]}",
                                },
                            },
                            {
                                "type": "section",
                                "text": {
                                    "type": "mrkdwn",
                                    "text": f"*Recommendations*\n{rec_text}",
                                },
                            },
                            {
                                "type": "context",
                                "elements": [
                                    {"type": "mrkdwn", "text": f"Incident ID: `{incident_id}` · OpsLens AI"}
                                ],
                            },
                        ],
                    }
                    _httpx.post(webhook_url, json=payload, timeout=10)
                    logger.info("Slack notification sent for incident %s", incident_id)
            except Exception as slack_exc:
                logger.warning("Slack notification failed for %s: %s", incident_id, slack_exc)

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
