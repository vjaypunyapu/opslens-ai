"""
OpsLens AI – Fast Log Alert + Exception Enricher
==================================================
Two-tier real-time log monitoring with known-issue suppression and team routing.

TIER 1 — Fast Alert (every 5 min, no LLM):
    Scans the last 5 minutes of logs for ERROR/EXCEPTION/CRITICAL lines.
    If the count exceeds LOG_FAST_ALERT_THRESHOLD, fires a Slack alert
    immediately (< 2 min detection-to-notification) with the raw error lines.

    Before firing:
      1. Checks KnownIssue table — suppresses if the error is acknowledged,
         snoozed, or linked to an open Jira ticket.
      2. Resolves AlertRoutingRule table — routes to each matching team's
         Slack webhook and email list (fan-out, priority-ordered).
      3. Falls back to LOG_FAST_ALERT_SLACK_WEBHOOK if no routing rules match.

TIER 2 — Exception Enrichment (triggered by Tier 1):
    For each new critical exception, queries the tenant's Qdrant vector store
    (Jira tickets, Slack messages, GitHub issues) to find related context.
    Sends an enriched follow-up Slack message to the same team channels.

Configuration (.env):
    LOG_FAST_ALERT_ENABLED          = true
    LOG_FAST_ALERT_WINDOW_MINUTES   = 5
    LOG_FAST_ALERT_THRESHOLD        = 3
    LOG_FAST_ALERT_COOLDOWN_MINUTES = 10
    LOG_FAST_ALERT_SLACK_WEBHOOK    =       # fallback if no routing rules match
    LOG_FAST_ALERT_ENRICH           = true
    LOG_FAST_ALERT_ENRICH_TOP_K     = 5
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import datetime, timedelta, timezone
from typing import NamedTuple

import httpx
import sqlalchemy as sa
from celery import shared_task
from celery.utils.log import get_task_logger

from apps.api.config import settings
from apps.worker.async_utils import run_async as _run_async

logger = get_task_logger(__name__)

# ── Regex: lines that warrant an immediate page ───────────────────────────────
_CRITICAL_RE = re.compile(
    r"(ERROR|CRITICAL|EXCEPTION|Traceback \(most recent|"
    r"raise\s+\w+Error|unhandled exception|task failed|"
    r"OOM|killed|segfault|500 Internal)",
    re.IGNORECASE,
)

# In-process cooldown store  {signature_hash: datetime_last_fired}
# Used as a fast local cache; the authoritative suppression check is the DB.
_cooldown_store: dict[str, datetime] = {}


# ── DB suppression check ──────────────────────────────────────────────────────
async def _is_known_issue_async(tenant_id: str, error_group: "ErrorGroup") -> tuple[bool, str]:
    """
    Returns (is_suppressed, reason).
    Checks KnownIssue table for:
      - Signature hash match
      - Regex/keyword pattern match against the error line
      - Snooze expiry (suppress_until)
      - Jira ticket auto-suppression (ticket not Done)
    """
    try:
        from apps.worker.db import AsyncSession
        from apps.api.models.log_ops import KnownIssue

        now = datetime.now(tz=timezone.utc)
        async with AsyncSession() as db:
            result = await db.execute(
                sa.select(KnownIssue).where(
                    KnownIssue.tenant_id == tenant_id,
                    KnownIssue.is_active == True,
                )
            )
            known_issues = result.scalars().all()

        for ki in known_issues:
            # Check snooze expiry first
            if ki.suppress_until and ki.suppress_until < now:
                continue  # snooze expired — no longer suppressed

            # Signature match
            if ki.signature and ki.signature == error_group.signature:
                reason = f"known issue (sig match)"
                if ki.jira_ticket_key:
                    reason += f" — tracked in {ki.jira_ticket_key}"
                if ki.description:
                    reason += f": {ki.description}"
                # Increment hit count asynchronously
                await _increment_ki_hit(tenant_id, str(ki.id))
                return True, reason

            # Pattern match against error line
            if ki.match_pattern:
                try:
                    if re.search(ki.match_pattern, error_group.first_line, re.IGNORECASE):
                        reason = f"known issue (pattern '{ki.match_pattern}')"
                        if ki.jira_ticket_key:
                            reason += f" — tracked in {ki.jira_ticket_key}"
                        await _increment_ki_hit(tenant_id, str(ki.id))
                        return True, reason
                except re.error:
                    if ki.match_pattern.lower() in error_group.first_line.lower():
                        await _increment_ki_hit(tenant_id, str(ki.id))
                        return True, f"known issue (keyword '{ki.match_pattern}')"

    except Exception as exc:
        logger.warning("Known issue check failed (will proceed with alert): %s", exc)

    return False, ""


async def _increment_ki_hit(tenant_id: str, ki_id: str) -> None:
    """Bump hit_count and last_hit_at on the KnownIssue row."""
    try:
        from apps.worker.db import AsyncSession
        from apps.api.models.log_ops import KnownIssue
        async with AsyncSession() as db:
            await db.execute(
                sa.update(KnownIssue)
                .where(KnownIssue.id == ki_id)
                .values(
                    hit_count=KnownIssue.hit_count + 1,
                    last_hit_at=datetime.now(tz=timezone.utc),
                )
            )
            await db.commit()
    except Exception:
        pass  # non-fatal


# ── DB routing resolution ─────────────────────────────────────────────────────
async def _resolve_routing_async(
    tenant_id: str,
    error_group: "ErrorGroup",
    container: str | None,
    fallback_webhook: str | None,
) -> list[dict]:
    """
    Returns list of {team_name, slack_webhook, email_recipients} dicts to notify.
    Rules are evaluated in priority order. stop_on_match halts further evaluation.
    Falls back to fallback_webhook if no rules match.
    """
    try:
        from apps.worker.db import AsyncSession
        from apps.api.models.log_ops import AlertRoutingRule
        from apps.api.routers.log_ops import _rule_matches

        async with AsyncSession() as db:
            result = await db.execute(
                sa.select(AlertRoutingRule).where(
                    AlertRoutingRule.tenant_id == tenant_id,
                    AlertRoutingRule.is_active == True,
                ).order_by(AlertRoutingRule.priority)
            )
            rules = result.scalars().all()

        targets: list[dict] = []
        for rule in rules:
            if _rule_matches(rule, error_group.first_line, container):
                targets.append({
                    "team_name":        rule.team_name,
                    "slack_webhook":    rule.slack_webhook,
                    "email_recipients": list(rule.email_recipients or []),
                    "pagerduty_key":    rule.pagerduty_key,
                })
                logger.info(
                    "Routing rule matched: team='%s' priority=%d for sig='%s'",
                    rule.team_name, rule.priority, error_group.signature,
                )
                if rule.stop_on_match:
                    break

        if not targets and fallback_webhook:
            targets.append({
                "team_name":        "Default",
                "slack_webhook":    fallback_webhook,
                "email_recipients": [],
            })

        return targets

    except Exception as exc:
        logger.warning("Routing resolution failed (using fallback): %s", exc)
        if fallback_webhook:
            return [{"team_name": "Default", "slack_webhook": fallback_webhook, "email_recipients": []}]
        return []


# ── Helpers ───────────────────────────────────────────────────────────────────
class ErrorGroup(NamedTuple):
    signature: str          # md5 of normalised first line
    first_line: str         # representative log line (cleaned)
    count: int              # occurrences in window
    sample_lines: list[str] # up to 5 lines for Slack preview


def _pull_logs(container: str, since_minutes: int) -> list[str]:
    try:
        r = subprocess.run(
            ["docker", "logs", "--since", f"{since_minutes}m",
             "--tail", "500", container],
            capture_output=True, text=True, timeout=20,
        )
        return (r.stdout + r.stderr).splitlines()
    except Exception as exc:
        logger.warning("docker logs failed for '%s': %s", container, exc)
        return []


def _collect_lines() -> list[str]:
    lines: list[str] = []
    window = settings.LOG_FAST_ALERT_WINDOW_MINUTES
    for c in (settings.LOG_SCAN_DOCKER_CONTAINERS or "").split(","):
        c = c.strip()
        if c:
            lines.extend(_pull_logs(c, window))
    if settings.LOG_SCAN_FILE_PATH:
        try:
            with open(settings.LOG_SCAN_FILE_PATH, errors="replace") as f:
                lines.extend(f.read().splitlines()[-500:])
        except Exception:
            pass
    return lines


def _extract_error_groups(lines: list[str]) -> list[ErrorGroup]:
    """Group ERROR/EXCEPTION lines by normalised signature."""
    groups: dict[str, dict] = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        if _CRITICAL_RE.search(line):
            # Grab traceback context (indented lines + Traceback header lines
            # which are NOT indented but required for frame extraction)
            block = [line]
            j = i + 1
            while j < len(lines) and (
                lines[j].startswith("  ")
                or lines[j].startswith("\t")
                or lines[j].startswith("Traceback (")
                or lines[j].startswith("During handling of")
                or lines[j].startswith("The above exception")
                or ("Error:" in lines[j] and not lines[j].startswith("20"))
                or ("Exception:" in lines[j] and not lines[j].startswith("20"))
            ):
                block.append(lines[j])
                j += 1

            # Signature = last meaningful line (actual exception class)
            key_line = block[-1]
            # Strip timestamps so the same error at different times deduplicates
            clean = re.sub(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}[.\d]*[Z]?", "", key_line).strip()
            sig = hashlib.md5(clean[:120].encode()).hexdigest()[:10]

            if sig in groups:
                groups[sig]["count"] += 1
            else:
                groups[sig] = {
                    "signature": sig,
                    "first_line": key_line[:300],
                    "count": 1,
                    "sample_lines": block[:30],  # enough for full multi-frame traceback
                }
            i = j
        else:
            i += 1

    return [
        ErrorGroup(
            signature=g["signature"],
            first_line=g["first_line"],
            count=g["count"],
            sample_lines=g["sample_lines"],
        )
        for g in sorted(groups.values(), key=lambda x: x["count"], reverse=True)[:10]
    ]


def _is_on_cooldown(sig: str) -> bool:
    last = _cooldown_store.get(sig)
    if not last:
        return False
    cooldown_secs = settings.LOG_FAST_ALERT_COOLDOWN_MINUTES * 60
    return (datetime.now(tz=timezone.utc) - last).total_seconds() < cooldown_secs


def _mark_fired(sig: str) -> None:
    _cooldown_store[sig] = datetime.now(tz=timezone.utc)


# ── RAG enrichment (queries Qdrant for related Jira/Slack/GitHub docs) ────────
def _enrich_with_rag(error_group: ErrorGroup, tenant_id: str) -> list[dict]:
    """
    Embed the error message and search the tenant's Qdrant collection for
    related documents (Jira tickets, Slack threads, GitHub issues, etc.).
    Returns a list of metadata dicts for the top-K matches.
    """
    try:
        from qdrant_client import QdrantClient
        from qdrant_client.models import Filter, FieldCondition, MatchAny

        # Build the search query from the exception signature
        query_text = (
            f"Error: {error_group.first_line}\n"
            + "\n".join(error_group.sample_lines[:3])
        )

        # Get embedding for the error text
        embedding = _get_embedding(query_text)
        if not embedding:
            return []

        qdrant = QdrantClient(
            url=settings.QDRANT_URL,
            api_key=settings.QDRANT_API_KEY or None,
            timeout=10,
        )
        collection = f"{settings.QDRANT_COLLECTION_PREFIX}{tenant_id}"

        # Optional: filter to only Jira + Slack + GitHub (skip e.g. Google Drive)
        search_filter = Filter(
            must=[
                FieldCondition(
                    key="source_type",
                    match=MatchAny(any=["jira", "slack", "github", "bitbucket"]),
                )
            ]
        )

        # qdrant-client >= 1.9 replaced .search() with .query_points()
        try:
            response = qdrant.query_points(
                collection_name=collection,
                query=embedding,
                limit=settings.LOG_FAST_ALERT_ENRICH_TOP_K,
                query_filter=search_filter,
                with_payload=True,
            )
        except Exception as filter_exc:
            # If the filtered query fails (e.g. 400 from dimension mismatch
            # or filter serialisation issue) fall back to an unfiltered query
            # so we still get results and can diagnose in logs.
            logger.warning(
                "RAG filtered query failed (%s) — retrying without filter", filter_exc
            )
            response = qdrant.query_points(
                collection_name=collection,
                query=embedding,
                limit=settings.LOG_FAST_ALERT_ENRICH_TOP_K,
                with_payload=True,
            )

        enriched = []
        for hit in response.points:
            payload = hit.payload or {}
            enriched.append({
                "source_type": payload.get("source_type", "unknown"),
                "title":       payload.get("title", "Untitled"),
                "url":         payload.get("url", ""),
                "author":      payload.get("author", ""),
                "snippet":     (payload.get("content_preview") or "")[:300],
                "score":       round(hit.score, 3),
            })
        return enriched

    except Exception as exc:
        logger.warning("RAG enrichment failed: %s", exc)
        return []


def _get_embedding(text: str) -> list[float] | None:
    """Get embedding synchronously using configured provider.

    IMPORTANT: the model used here MUST match the model used in ingestion.py
    (embed_and_upsert) so query vectors have the same dimensions as stored
    vectors.  ingestion.py hardcodes "text-embedding-3-small" (1536-dim);
    we mirror that here instead of reading settings.OPENAI_EMBED_MODEL which
    could differ from what was used at ingest-time.
    """
    # Mirror ingestion.py: hardcoded to "text-embedding-3-small" (1536-dim).
    # Change both files together if you switch embed models.
    _INGEST_EMBED_MODEL = "text-embedding-3-small"
    try:
        if settings.LLM_PROVIDER == "ollama":
            resp = httpx.post(
                f"{settings.OLLAMA_URL}/api/embed",
                json={"model": settings.OLLAMA_EMBED_MODEL, "input": text},
                timeout=120,
            )
            resp.raise_for_status()
            return resp.json()["embeddings"][0]
        else:
            import openai
            client = openai.OpenAI(api_key=settings.OPENAI_API_KEY)
            resp = client.embeddings.create(
                model=_INGEST_EMBED_MODEL,
                input=text[:2000],  # cap to avoid token limits
            )
            return resp.data[0].embedding
    except Exception as exc:
        logger.warning("Embedding failed: %s", exc)
        return None


# ── LLM one-liner diagnosis (optional, non-blocking) ─────────────────────────
def _llm_diagnosis(error_group: ErrorGroup, related_docs: list[dict]) -> str:
    """
    Ask the LLM: given this exception and these related docs, what is likely
    wrong and what should the on-call engineer check first?
    Returns a 2-3 sentence diagnosis, or empty string on failure.
    """
    try:
        related_text = ""
        for d in related_docs[:3]:
            related_text += (
                f"\n- [{d['source_type'].upper()}] {d['title']}: {d['snippet'][:200]}"
            )

        prompt = (
            f"An exception occurred in production:\n\n"
            f"  {error_group.first_line}\n\n"
            f"Related items found in our knowledge base:{related_text or ' (none)'}\n\n"
            f"In 2-3 sentences: what is likely wrong, and what is the first thing "
            f"the on-call engineer should check? Be specific and actionable."
        )

        provider = settings.LLM_PROVIDER

        if provider == "claude":
            import anthropic
            client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
            msg = client.messages.create(
                model=settings.ANTHROPIC_CHAT_MODEL,
                max_tokens=256,
                messages=[{"role": "user", "content": prompt}],
            )
            return msg.content[0].text.strip()

        elif provider == "ollama":
            resp = httpx.post(
                f"{settings.OLLAMA_URL}/api/generate",
                json={"model": settings.OLLAMA_CHAT_MODEL, "prompt": prompt, "stream": False},
                timeout=300,
            )
            resp.raise_for_status()
            return resp.json().get("response", "").strip()

        else:  # openai
            import openai
            client = openai.OpenAI(api_key=settings.OPENAI_API_KEY)
            resp = client.chat.completions.create(
                model=settings.OPENAI_CHAT_MODEL,
                temperature=0.0,
                max_tokens=256,
                messages=[{"role": "user", "content": prompt}],
            )
            return (resp.choices[0].message.content or "").strip()

    except Exception as exc:
        logger.warning("LLM diagnosis failed: %s", exc)
        return ""


# ── Slack formatter ───────────────────────────────────────────────────────────
def _build_slack_payload(
    error_group: ErrorGroup,
    related_docs: list[dict],
    diagnosis: str,
    tenant_id: str,
) -> dict:
    now_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Group related docs by source type for clean display
    by_source: dict[str, list[dict]] = {}
    for d in related_docs:
        by_source.setdefault(d["source_type"], []).append(d)

    # Build the exception block
    error_preview = "\n".join(error_group.sample_lines[:4])
    if len(error_preview) > 600:
        error_preview = error_preview[:600] + "…"

    blocks: list[dict] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"🚨 Exception Detected — {tenant_id}"},
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"*{now_str}*  •  occurred *{error_group.count}x* in last {settings.LOG_FAST_ALERT_WINDOW_MINUTES} min"}
            ],
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Exception:*\n```{error_preview}```",
            },
        },
    ]

    # LLM diagnosis block
    if diagnosis:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*🤖 AI Diagnosis:*\n{diagnosis}",
            },
        })

    blocks.append({"type": "divider"})

    # Related context blocks — one per source type
    if related_docs:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*📎 Related context from connected sources:*",
            },
        })

        source_emojis = {
            "jira":   "🎫",
            "slack":  "💬",
            "github": "🐙",
        }

        for source_type, docs in by_source.items():
            emoji = source_emojis.get(source_type, "📄")
            lines = []
            for d in docs[:3]:  # max 3 per source
                title = d["title"] or "Untitled"
                url = d["url"]
                snippet = d["snippet"][:120].replace("\n", " ")
                if url:
                    lines.append(f"• *<{url}|{title}>*\n  _{snippet}_")
                else:
                    lines.append(f"• *{title}*\n  _{snippet}_")

            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"{emoji} *{source_type.upper()}*\n" + "\n".join(lines),
                },
            })

    else:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "_No related Jira tickets, Slack threads, or GitHub issues found in knowledge base._\n"
                        "_Tip: sync your integrations to enable contextual enrichment._",
            },
        })

    blocks.append({
        "type": "context",
        "elements": [
            {"type": "mrkdwn", "text": "Powered by *OpsLens AI Fast Alert*"}
        ],
    })

    return {
        "text": f"🚨 Exception in {tenant_id}: {error_group.first_line[:80]}",
        "blocks": blocks,
    }


def _send_slack(webhook_url: str, payload: dict) -> bool:
    try:
        resp = httpx.post(webhook_url, json=payload, timeout=10)
        return resp.status_code == 200
    except Exception as exc:
        logger.error("Slack fast-alert failed: %s", exc)
        return False


# ── Timeline event writer ─────────────────────────────────────────────────────
async def _write_timeline_event_async(
    tenant_id: str,
    error_group: "ErrorGroup",
    routing_targets: list[dict],
    window_minutes: int,
) -> str | None:
    """
    Persist a log_anomaly TimelineEvent so the delivery risk timeline
    has a record of the exception spike. Returns the new event ID.
    """
    try:
        import uuid as _uuid
        from apps.worker.db import AsyncSession
        from apps.api.models.timeline import TimelineEvent

        routed_teams = [t.get("team_name", "Default") for t in routing_targets]
        event = TimelineEvent(
            id           = str(_uuid.uuid4()),
            tenant_id    = tenant_id,
            source_type  = "log_anomaly",
            occurred_at  = datetime.now(tz=timezone.utc),
            title        = (
                f"Exception spike: {error_group.first_line[:120]} "
                f"(×{error_group.count} in {window_minutes}m)"
            ),
            description  = "\n".join(error_group.sample_lines[:3])[:500],
            service      = None,        # container name not threaded here — can be added
            team         = routed_teams[0] if routed_teams else None,
            actor        = "opslens-fast-scan",
            is_anomaly   = True,
            severity     = "high" if error_group.count >= 10 else "medium",
            external_url = None,
            event_metadata = {
                "error_signature": error_group.signature,
                "error_count":     error_group.count,
                "window_minutes":  window_minutes,
                "routed_teams":    routed_teams,
            },
            source_id = f"log_anomaly:{tenant_id}:{error_group.signature}",
        )
        async with AsyncSession() as db:
            # Upsert: don't duplicate if this signature already logged recently
            existing = await db.execute(
                sa.select(TimelineEvent.id).where(
                    TimelineEvent.tenant_id == tenant_id,
                    TimelineEvent.source_id == event.source_id,
                    TimelineEvent.occurred_at >= datetime.now(tz=timezone.utc)
                    - timedelta(minutes=window_minutes * 2),
                ).limit(1)
            )
            if existing.scalar_one_or_none():
                logger.debug("Timeline: skipping duplicate log_anomaly for sig=%s", error_group.signature)
                return None
            db.add(event)
            await db.commit()
            logger.info(
                "Timeline: log_anomaly event saved for sig=%s (tenant=%s)",
                error_group.signature, tenant_id,
            )
            return event.id
    except Exception as exc:
        logger.warning("Timeline event write failed (non-fatal): %s", exc)
        return None


# ── Celery task: per-exception enrichment + notification ─────────────────────
@shared_task(
    name="logs.enrich_and_alert",
    bind=True,
    max_retries=1,
    soft_time_limit=120,
    time_limit=150,
)
def enrich_and_alert(
    self,
    tenant_id: str,
    error_group_dict: dict,
    webhook_url: str,
    routing_targets: list[dict] | None = None,
    error_count: int = 1,
    window_minutes: int = 5,
):
    """
    Given a detected exception:
    1. Query Qdrant for related Jira tickets / Slack threads / GitHub issues
    2. Send an enriched Slack notification with context
    3. Dispatch RRT brief generation (structured incident artifact)
    """
    try:
        eg = ErrorGroup(**error_group_dict)

        # 1. RAG enrichment
        related = []
        if settings.LOG_FAST_ALERT_ENRICH:
            logger.info("Enriching exception '%s' for tenant=%s", eg.signature, tenant_id)
            related = _enrich_with_rag(eg, tenant_id)
            logger.info("Found %d related docs for exception '%s'", len(related), eg.signature)

        # 2. LLM diagnosis (quick one-liner for the enriched alert)
        diagnosis = ""
        if settings.LOG_FAST_ALERT_ENRICH and related:
            diagnosis = _llm_diagnosis(eg, related)

        # 3. Send enriched notification (context-rich follow-up to raw alert)
        from apps.api.services.notifications import (
            build_enriched_alert_payload, send_webhook as _send_notification,
        )
        payload = build_enriched_alert_payload(
            error_first_line=eg.first_line,
            error_preview="\n".join(eg.sample_lines[:4])[:600],
            error_count=eg.count,
            window_minutes=window_minutes,
            diagnosis=diagnosis,
            related_docs=related,
            tenant_id=tenant_id,
        )
        ok = _send_notification(webhook_url, payload)
        if ok:
            logger.info("Enriched alert sent for exception '%s' tenant=%s", eg.signature, tenant_id)
        else:
            logger.error("Slack delivery failed for exception '%s'", eg.signature)

        # 4. Dispatch full RRT brief generation (structured artifact with full lifecycle)
        try:
            from apps.worker.tasks.rrt_briefing import generate_rrt_brief
            targets = routing_targets or [{"team_name": "Default", "slack_webhook": webhook_url, "email_recipients": []}]
            generate_rrt_brief.delay(
                tenant_id=tenant_id,
                error_group_dict=error_group_dict,
                related_items=related,
                routing_targets=targets,
                error_count=error_count,
                window_minutes=window_minutes,
            )
            logger.info("RRT brief generation dispatched for exception '%s'", eg.signature)
        except Exception as rrt_exc:
            logger.warning("RRT brief dispatch failed (non-fatal): %s", rrt_exc)

        return {"sent": ok, "related_docs": len(related)}

    except Exception as exc:
        logger.exception("enrich_and_alert failed: %s", exc)
        raise self.retry(exc=exc, countdown=30)


# ── Celery task: fast scanner — runs every 5 minutes ─────────────────────────
@shared_task(
    name="logs.fast_scan",
    bind=True,
    max_retries=1,
    soft_time_limit=60,
    time_limit=90,
)
def fast_scan(self, tenant_id: str | None = None):
    """
    Scan the last LOG_FAST_ALERT_WINDOW_MINUTES of logs.
    For each new critical error group (not on cooldown):
      - Fire a raw Slack alert immediately (no LLM delay)
      - Dispatch enrich_and_alert asynchronously for contextual enrichment
    """
    if not settings.LOG_FAST_ALERT_ENABLED:
        return {"status": "disabled"}

    try:
        lines = _collect_lines()
        if not lines:
            return {"status": "no_logs"}

        groups = _extract_error_groups(lines)
        new_groups = [g for g in groups if not _is_on_cooldown(g.signature)]

        if not new_groups:
            logger.debug("Fast scan: %d error groups, all on cooldown", len(groups))
            return {"status": "all_on_cooldown", "groups": len(groups)}

        # Filter by threshold: only fire if count >= threshold
        threshold = settings.LOG_FAST_ALERT_THRESHOLD
        actionable = [g for g in new_groups if g.count >= threshold]

        if not actionable:
            logger.info(
                "Fast scan: %d new groups found but none meet threshold (%d errors required)",
                len(new_groups), threshold,
            )
            return {"status": "below_threshold", "new_groups": len(new_groups)}

        import asyncio

        fired = 0
        suppressed = 0
        tid = tenant_id or "system"
        fallback_webhook = settings.LOG_FAST_ALERT_SLACK_WEBHOOK or settings.LOG_SCAN_SLACK_WEBHOOK

        for eg in actionable:
            # ── Step 1: Known issue suppression (DB check) ────────────────────
            is_suppressed, suppress_reason = _run_async(
                _is_known_issue_async(tid, eg)
            )
            if is_suppressed:
                logger.info(
                    "Suppressed exception '%s' (%s) — %s",
                    eg.signature, eg.first_line[:80], suppress_reason,
                )
                suppressed += 1
                continue

            # ── Step 2: In-process cooldown (fast dedup within same worker) ───
            _mark_fired(eg.signature)

            # ── Step 3: Resolve team routing ──────────────────────────────────
            # (timeline event written after routing is resolved, below)
            targets = _run_async(
                _resolve_routing_async(tid, eg, container=None, fallback_webhook=fallback_webhook)
            )
            if not targets:
                logger.warning(
                    "No routing targets for exception '%s' — configure routing rules or set LOG_FAST_ALERT_SLACK_WEBHOOK",
                    eg.signature,
                )
                continue

            # ── Step 4: Write timeline event (non-blocking) ───────────────────
            _run_async(
                _write_timeline_event_async(
                    tenant_id=tid,
                    error_group=eg,
                    routing_targets=targets,
                    window_minutes=settings.LOG_FAST_ALERT_WINDOW_MINUTES,
                )
            )

            # ── Step 4b: PagerDuty — fire for any rule with a routing key ─────
            try:
                from apps.worker.tasks.pagerduty import dispatch_pagerduty_alerts
                sev = "critical" if eg.count >= 10 else "high"
                dispatch_pagerduty_alerts(
                    routing_targets=targets,
                    tenant_id=tid,
                    error_signature=eg.signature,
                    error_summary=eg.first_line[:120],
                    service_name=None,
                    severity=sev,
                    custom_details={
                        "error_count": eg.count,
                        "window_minutes": settings.LOG_FAST_ALERT_WINDOW_MINUTES,
                        "sample": eg.first_line[:300],
                    },
                )
            except Exception as pd_exc:
                logger.warning("PagerDuty dispatch failed (non-fatal): %s", pd_exc)

            # ── Step 5: Fire immediate raw alert to each team ─────────────────
            from apps.api.services.notifications import (
                build_fast_alert_payload, send_webhook as _send_notification,
            )
            for target in targets:
                webhook_url = target.get("slack_webhook")
                team_name   = target.get("team_name", "Team")
                if not webhook_url:
                    continue

                raw_payload = build_fast_alert_payload(
                    error_lines=eg.sample_lines,
                    error_count=eg.count,
                    window_minutes=settings.LOG_FAST_ALERT_WINDOW_MINUTES,
                    team_name=team_name,
                    tenant_id=tid,
                )
                _send_notification(webhook_url, raw_payload)

                # ── Step 6: Dispatch enrichment + RRT brief per team ──────────
                enrich_and_alert.delay(
                    tenant_id=tid,
                    error_group_dict={
                        "signature":    eg.signature,
                        "first_line":   eg.first_line,
                        "count":        eg.count,
                        "sample_lines": eg.sample_lines,
                    },
                    webhook_url=webhook_url,
                    routing_targets=targets,
                    error_count=eg.count,
                    window_minutes=settings.LOG_FAST_ALERT_WINDOW_MINUTES,
                )

            fired += 1
            logger.info(
                "Fast alert fired for exception '%s' to %d team(s) (count=%d)",
                eg.signature, len(targets), eg.count,
            )

        return {
            "status": "ok",
            "lines_scanned": len(lines),
            "groups_found": len(groups),
            "alerts_fired": fired,
            "suppressed": suppressed,
        }

    except Exception as exc:
        logger.exception("fast_scan failed: %s", exc)
        raise self.retry(exc=exc, countdown=30)
