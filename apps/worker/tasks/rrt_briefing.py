"""
OpsLens AI — RRT Briefing Generator
======================================
Generates a structured Rapid Response Team (RRT) brief when a critical
exception is detected, enriched with RAG context (Jira/GitHub/Slack),
and delivers it to Slack as a formatted, threadable artifact.

The brief is stored in the DB with a stable ID and full lifecycle tracking
(open → investigating → resolved).  Follow-up status updates are posted
as Slack thread replies, keeping all incident context in one place.

Triggered by: log_fast_alert.enrich_and_alert (after RAG enrichment)
Also callable: POST /api/v1/rrt-briefs/generate (on-demand)

Slack message structure:
  ┌─────────────────────────────────────────┐
  │ 🚨 [OPEN] Incident Brief — <title>      │
  │─────────────────────────────────────────│
  │ 📋 WHAT HAPPENED                        │
  │ ⚡ IMPACT                               │
  │ 🕐 STARTED  /  👤 OWNER                │
  │─────────────────────────────────────────│
  │ 🔍 SUSPECTED CAUSE (LLM)               │
  │─────────────────────────────────────────│
  │ 📎 RELATED CONTEXT                      │
  │   🎫 Jira tickets                       │
  │   🐙 GitHub PRs / issues               │
  │   💬 Slack threads                      │
  │─────────────────────────────────────────│
  │ ✅ NEXT ACTIONS (numbered)              │
  │─────────────────────────────────────────│
  │ Brief ID · Status · Update link         │
  └─────────────────────────────────────────┘
"""
from __future__ import annotations

import base64
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import TypedDict

import httpx
from celery import shared_task
from celery.utils.log import get_task_logger

from apps.api.config import settings
from apps.worker.async_utils import run_async as _run_async

logger = get_task_logger(__name__)

# ── Stack trace parsers ───────────────────────────────────────────────────────

# Python:  File "/app/payments/handlers.py", line 87, in handle_request
_PY_FRAME_RE = re.compile(
    r'File "([^"]+)", line (\d+), in (\S+)'
)
# Node.js: at PaymentService.charge (/app/services/payment.js:134:18)
#          at /app/worker/index.js:42:10
_NODE_FRAME_RE = re.compile(
    r'at (?:\S+ \()?([^\s()]+\.(?:js|ts|mjs|cjs)):(\d+):\d+\)?'
)
# Java/Kotlin: at com.acme.payments.PaymentService.charge(PaymentService.java:87)
_JAVA_FRAME_RE = re.compile(
    r'at [\w.$]+\.([\w$]+)\((\w+\.(?:java|kt|scala)):(\d+)\)'
)

# Paths to skip — stdlib, venv, bundled deps, not your code
_SKIP_PATH_RE = re.compile(
    r'(site-packages|dist-packages|/usr/lib/python|/usr/local/lib/python'
    r'|\.venv|venv/|node_modules/|<frozen|<string|<stdin'
    r'|celery/|asyncio/|starlette/|fastapi/|uvicorn/|httpx/)',
    re.IGNORECASE,
)

# Common container path prefixes to strip to get repo-relative paths
_PATH_PREFIXES = ["/app/", "/home/app/", "/usr/src/app/", "/code/", "/srv/", "./"]


def _parse_traceback_frames(lines: list[str]) -> list[dict]:
    """
    Parse a stack trace and return a list of app-code frames:
      [{"file": "payments/handlers.py", "line": 87, "function": "handle_request"}, ...]

    Supports Python, Node.js, and Java/Kotlin.
    Filters out stdlib, venv, and framework internals automatically.
    """
    frames: list[dict] = []
    seen: set[str] = set()

    for line in lines:
        matched = None

        m = _PY_FRAME_RE.search(line)
        if m:
            matched = {"file": m.group(1), "line": int(m.group(2)), "function": m.group(3)}

        if not matched:
            m = _NODE_FRAME_RE.search(line)
            if m:
                matched = {"file": m.group(1), "line": int(m.group(2)), "function": ""}

        if not matched:
            m = _JAVA_FRAME_RE.search(line)
            if m:
                matched = {"file": m.group(2), "line": int(m.group(3)), "function": m.group(1)}

        if not matched:
            continue

        path = matched["file"]

        # Skip non-app code
        if _SKIP_PATH_RE.search(path):
            continue

        # Normalise to repo-relative path
        for prefix in _PATH_PREFIXES:
            if path.startswith(prefix):
                path = path[len(prefix):]
                break

        dedup_key = f"{path}:{matched['line']}"
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        frames.append({"file": path, "line": matched["line"], "function": matched["function"]})

    # Return innermost frames first (most relevant to the error), max 5
    return frames[-5:][::-1]


# ── GitHub code context fetcher ───────────────────────────────────────────────

async def _fetch_github_code_context(
    frames: list[dict],
    tenant_id: str,
    context_lines: int = 10,
) -> list[dict]:
    """
    For each stack frame, fetch the relevant lines from GitHub and the last
    commit that touched that file.

    Returns a list of code_frame dicts:
    {
        file, line, function, repo,
        snippet,              # the ±context_lines around the error line
        last_commit_sha,
        last_commit_msg,
        last_commit_author,
        last_commit_url,
        github_url,           # permalink to the line on github.com
    }
    """
    if not frames:
        return []

    try:
        import sqlalchemy as sa
        from apps.worker.db import AsyncSession
        from apps.api.db.models import Integration
        from apps.api.utils.crypto import decrypt_credentials

        # Load GitHub integration for this tenant
        async with AsyncSession() as db:
            result = await db.execute(
                sa.select(Integration).where(
                    Integration.tenant_id == tenant_id,
                    Integration.source_type == "github",
                    Integration.status == "active",
                ).limit(1)
            )
            intg = result.scalar_one_or_none()

        if not intg:
            logger.info("No active GitHub integration for tenant=%s — skipping code context", tenant_id)
            return []

        creds = decrypt_credentials(intg.credentials)
        token = creds.get("access_token", "")
        if not token:
            return []

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        # Discover accessible repos (cached from last sync ideally, else quick API call)
        async with httpx.AsyncClient(headers=headers, timeout=15) as client:
            repos_resp = await client.get(
                "https://api.github.com/user/repos",
                params={"per_page": 100, "sort": "updated", "affiliation": "owner,collaborator"},
            )
            repos = repos_resp.json() if repos_resp.is_success else []
            repo_names = [r["full_name"] for r in repos if isinstance(r, dict)]

        code_frames: list[dict] = []

        async with httpx.AsyncClient(headers=headers, timeout=15) as client:
            for frame in frames:
                rel_path = frame["file"]
                line_no = frame["line"]

                # Try each repo until we find the file
                for full_name in repo_names:
                    content_resp = await client.get(
                        f"https://api.github.com/repos/{full_name}/contents/{rel_path}",
                    )
                    if not content_resp.is_success:
                        continue

                    content_data = content_resp.json()
                    if content_data.get("encoding") != "base64":
                        continue

                    # Decode file content
                    file_text = base64.b64decode(content_data["content"]).decode("utf-8", errors="replace")
                    all_lines = file_text.splitlines()
                    total = len(all_lines)

                    # Extract ±context_lines around the error line
                    start = max(0, line_no - context_lines - 1)
                    end   = min(total, line_no + context_lines)
                    snippet_lines = all_lines[start:end]

                    # Number the snippet lines
                    numbered = []
                    for i, sl in enumerate(snippet_lines, start=start + 1):
                        marker = "→ " if i == line_no else "  "
                        numbered.append(f"{marker}{i:4d} | {sl}")
                    snippet = "\n".join(numbered)

                    # Get last commit for this file
                    commit_msg = ""
                    commit_sha = ""
                    commit_author = ""
                    commit_url = ""
                    commits_resp = await client.get(
                        f"https://api.github.com/repos/{full_name}/commits",
                        params={"path": rel_path, "per_page": 1},
                    )
                    if commits_resp.is_success:
                        commits = commits_resp.json()
                        if commits:
                            c = commits[0]
                            commit_sha = c.get("sha", "")[:7]
                            commit_url = c.get("html_url", "")
                            commit_msg = c.get("commit", {}).get("message", "").splitlines()[0][:120]
                            commit_author = (
                                c.get("commit", {}).get("author", {}).get("name", "")
                                or c.get("author", {}).get("login", "")
                            )

                    # Detect language from extension
                    ext = rel_path.rsplit(".", 1)[-1] if "." in rel_path else ""
                    lang_map = {"py": "python", "js": "javascript", "ts": "typescript",
                                "java": "java", "kt": "kotlin", "rb": "ruby",
                                "go": "go", "rs": "rust", "cs": "csharp"}
                    language = lang_map.get(ext, ext or "text")

                    github_url = (
                        f"https://github.com/{full_name}/blob/HEAD/{rel_path}#L{line_no}"
                    )

                    code_frames.append({
                        "file":                rel_path,
                        "line":                line_no,
                        "function":            frame.get("function", ""),
                        "repo":                full_name,
                        "snippet":             snippet,
                        "language":            language,
                        "last_commit_sha":     commit_sha,
                        "last_commit_msg":     commit_msg,
                        "last_commit_author":  commit_author,
                        "last_commit_url":     commit_url,
                        "github_url":          github_url,
                    })

                    logger.info(
                        "Code context: found %s:%d in repo %s (last commit: %s by %s)",
                        rel_path, line_no, full_name, commit_sha, commit_author,
                    )
                    break  # found the file — don't try other repos

        return code_frames

    except Exception as exc:
        logger.warning("GitHub code context fetch failed (non-fatal): %s", exc)
        return []


# ── TypedDicts matching ErrorGroup / enrichment output ───────────────────────
class RelatedItem(TypedDict):
    source_type: str   # jira | github | slack
    title: str
    url: str
    snippet: str
    score: float


# ── Timeline context fetcher ──────────────────────────────────────────────────
async def _fetch_timeline_context(tenant_id: str, detected_at: datetime, window_minutes: int = 60) -> list[dict]:
    """
    Fetch timeline events (PRs, deploys, Jira transitions) from the
    window_minutes before the incident. These are used to answer
    "what changed right before this spike?".
    Returns a list of lightweight dicts suitable for the LLM prompt.
    """
    try:
        import sqlalchemy as sa
        from apps.worker.db import AsyncSession
        from apps.api.models.timeline import TimelineEvent

        start = detected_at - timedelta(minutes=window_minutes)
        # Also look slightly ahead (5 min) in case of clock skew
        end = detected_at + timedelta(minutes=5)

        async with AsyncSession() as db:
            result = await db.execute(
                sa.select(TimelineEvent)
                .where(
                    TimelineEvent.tenant_id == tenant_id,
                    TimelineEvent.occurred_at >= start,
                    TimelineEvent.occurred_at <= end,
                    # Exclude our own log_anomaly events to avoid circular references
                    TimelineEvent.source_type.in_([
                        "github_pr", "github_commit", "github_deploy",
                        "jira_issue", "manual",
                    ]),
                )
                .order_by(TimelineEvent.occurred_at.desc())
                .limit(15)
            )
            events = result.scalars().all()

        return [
            {
                "type":     e.source_type,
                "title":    e.title,
                "actor":    e.actor or "",
                "service":  e.service or "",
                "occurred": e.occurred_at.strftime("%H:%M UTC"),
                "severity": e.severity or "info",
                "url":      e.external_url or "",
            }
            for e in events
        ]

    except Exception as exc:
        logger.warning("Timeline context fetch failed (non-fatal): %s", exc)
        return []


# ── LLM: generate structured RRT fields ──────────────────────────────────────
def _generate_brief_fields(
    error_first_line: str,
    error_sample: list[str],
    related_items: list[RelatedItem],
    owner_team: str | None,
    detected_at: datetime,
    timeline_events: list[dict] | None = None,
    code_frames: list[dict] | None = None,
) -> dict:
    """
    Ask the LLM to produce a JSON object with:
      title, what_happened, impact, suspected_cause, next_actions
    """
    related_text = ""
    for item in related_items[:5]:
        related_text += (
            f"\n- [{item['source_type'].upper()}] {item['title']}: "
            f"{item['snippet'][:200]}"
        )

    # Timeline changes in the 60 min before the incident (the "what changed" signal)
    timeline_text = ""
    for ev in (timeline_events or [])[:10]:
        actor_part = f" by {ev['actor']}" if ev["actor"] else ""
        svc_part   = f" ({ev['service']})" if ev["service"] else ""
        timeline_text += f"\n- [{ev['type'].upper()}] {ev['title']}{svc_part}{actor_part} at {ev['occurred']}"

    # Build code context block for the prompt
    code_text = ""
    if code_frames:
        for cf in code_frames[:3]:
            commit_info = ""
            if cf.get("last_commit_sha"):
                commit_info = (
                    f"\n  Last change: [{cf['last_commit_sha']}] "
                    f"\"{cf['last_commit_msg']}\" by {cf['last_commit_author']}"
                )
            code_text += (
                f"\n\nFile: {cf['file']} (line {cf['line']}, function `{cf['function']}`)"
                f"{commit_info}"
                f"\n```{cf.get('language','')}\n{cf['snippet']}\n```"
            )

    prompt = f"""\
You are an SRE assistant generating an incident brief for a production exception.

EXCEPTION:
{chr(10).join(error_sample[:6])}

SOURCE CODE AT THE CRASH POINT:{code_text or ' (GitHub not connected or file not found in repo)'}

RELATED CONTEXT (from Jira, GitHub, Slack):{related_text or ' (none found)'}

CHANGES IN THE 60 MINUTES BEFORE THIS INCIDENT (deployments, PRs, Jira transitions):{timeline_text or ' (none recorded — connect GitHub/Jira to enable this)'}

OWNER TEAM: {owner_team or 'Unknown'}
DETECTED AT: {detected_at.strftime('%Y-%m-%d %H:%M UTC')}

Generate a structured incident brief as a JSON object with exactly these fields:
{{
  "title": "<8-word max headline, e.g. 'Payment service timeout spike after deploy'>",
  "what_happened": "<2-3 sentences: what failed, where, how many times>",
  "impact": "<1-2 sentences: which services or user flows are affected>",
  "suspected_cause": "<2-3 sentences: root cause referencing the ACTUAL CODE if available — cite the specific file, line number, and what the code is doing wrong. If a recent commit changed this code, call out the commit and author explicitly>",
  "next_actions": [
    "<specific action 1 — name the exact file, function, line number, or command>",
    "<specific action 2>",
    "<specific action 3>"
  ]
}}

Be specific and actionable. If source code is available above, use it — reference exact line numbers, variable names, and the specific logic that is failing.
Return ONLY the JSON object, no explanation."""

    try:
        raw = _call_llm(prompt)
        # Strip markdown code fences if present
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw.strip())
    except (json.JSONDecodeError, Exception) as exc:
        logger.warning("LLM brief generation failed (%s) — using defaults", exc)
        return {
            "title": error_first_line[:60],
            "what_happened": f"Exception detected: {error_first_line[:200]}",
            "impact": "Impact under investigation.",
            "suspected_cause": "Root cause under investigation. See related context.",
            "next_actions": [
                "Review the full stack trace in the logs",
                "Check recent deployments and PRs for related changes",
                "Escalate to the owning team if not resolved within 15 minutes",
            ],
        }


def _call_llm(prompt: str) -> str:
    provider = settings.LLM_PROVIDER
    if provider == "claude":
        import anthropic
        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        msg = client.messages.create(
            model=settings.ANTHROPIC_CHAT_MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text
    elif provider == "ollama":
        resp = httpx.post(
            f"{settings.OLLAMA_URL}/api/generate",
            json={"model": settings.OLLAMA_CHAT_MODEL, "prompt": prompt, "stream": False},
            timeout=300,
        )
        resp.raise_for_status()
        return resp.json().get("response", "")
    else:
        import openai
        client = openai.OpenAI(api_key=settings.OPENAI_API_KEY)
        resp = client.chat.completions.create(
            model=settings.OPENAI_CHAT_MODEL,
            temperature=0.0,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.choices[0].message.content or ""


# ── Slack delivery ────────────────────────────────────────────────────────────
def _build_slack_brief(brief_id: str, fields: dict, related_items: list[RelatedItem],
                       owner_team: str | None, detected_at: datetime,
                       error_count: int, window_minutes: int,
                       timeline_events: list[dict] | None = None) -> dict:
    """Build a rich Slack Block Kit message for the RRT brief."""

    status_emoji = "🔴"
    status_label = "OPEN"

    source_emojis = {"jira": "🎫", "github": "🐙", "slack": "💬"}

    # Related context lines
    related_lines = []
    for item in related_items[:6]:
        emoji = source_emojis.get(item["source_type"], "📄")
        url = item.get("url", "")
        title = item.get("title", "Untitled")
        snippet = item.get("snippet", "")[:100].replace("\n", " ")
        if url:
            related_lines.append(f"{emoji} *<{url}|{title}>*\n  _{snippet}_")
        else:
            related_lines.append(f"{emoji} *{title}*\n  _{snippet}_")

    related_text = "\n".join(related_lines) if related_lines else \
        "_No related tickets, PRs, or threads found. Sync more integrations to enable this._"

    # Next actions as numbered list
    actions = fields.get("next_actions", [])
    actions_text = "\n".join(f"{i+1}. {a}" for i, a in enumerate(actions[:5]))

    blocks = [
        # ── Header ───────────────────────────────────────────────────────────
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"{status_emoji} [{status_label}] Incident Brief — {fields.get('title', 'Exception Detected')}",
            },
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": (
                        f"Detected *{detected_at.strftime('%Y-%m-%d %H:%M UTC')}*  •  "
                        f"Occurred *{error_count}x* in last {window_minutes} min  •  "
                        f"Owner: *{owner_team or 'Unassigned'}*  •  "
                        f"Brief ID: `{brief_id[:8]}`"
                    ),
                }
            ],
        },
        {"type": "divider"},

        # ── What happened + Impact ────────────────────────────────────────────
        {
            "type": "section",
            "fields": [
                {
                    "type": "mrkdwn",
                    "text": f"*📋 What Happened*\n{fields.get('what_happened', 'Under investigation.')}",
                },
                {
                    "type": "mrkdwn",
                    "text": f"*⚡ Impact*\n{fields.get('impact', 'Under investigation.')}",
                },
            ],
        },
        {"type": "divider"},

        # ── Suspected cause ───────────────────────────────────────────────────
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*🔍 Suspected Cause*\n{fields.get('suspected_cause', 'Under investigation.')}",
            },
        },
        {"type": "divider"},

        # ── Related context ───────────────────────────────────────────────────
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*📎 Related Context (Jira · GitHub · Slack)*\n{related_text}",
            },
        },
        {"type": "divider"},

        # ── Timeline: what changed before the incident ─────────────────────────
        *_build_timeline_blocks(timeline_events or []),

        # ── Next actions ──────────────────────────────────────────────────────
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*✅ Recommended Next Actions*\n{actions_text}",
            },
        },
        {"type": "divider"},

        # ── Footer ────────────────────────────────────────────────────────────
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": (
                        f"Reply in thread to add updates  •  "
                        f"Update status: `PATCH /api/v1/rrt-briefs/{brief_id}`  •  "
                        f"Powered by *OpsLens AI*"
                    ),
                }
            ],
        },
    ]

    return {
        "text": f"{status_emoji} [{status_label}] Incident Brief: {fields.get('title', 'Exception Detected')}",
        "blocks": blocks,
    }


def _build_timeline_blocks(timeline_events: list[dict]) -> list[dict]:
    """
    Build 0-2 Slack blocks showing recent PRs / deploys / Jira transitions
    that preceded the incident (the "What Changed" section).
    """
    if not timeline_events:
        return [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "*🕐 Changes Before This Incident*\n"
                        "_No GitHub/Jira events recorded in the 60 min before this incident. "
                        "Connect GitHub and Jira webhooks to enable automatic change correlation._"
                    ),
                },
            },
            {"type": "divider"},
        ]

    type_emojis = {
        "github_pr":     "🔀",
        "github_commit": "📝",
        "github_deploy": "🚀",
        "jira_issue":    "🎫",
        "manual":        "📌",
    }
    lines = []
    for ev in timeline_events[:8]:
        emoji = type_emojis.get(ev["type"], "•")
        actor = f" by {ev['actor']}" if ev.get("actor") else ""
        url   = ev.get("url", "")
        title = ev.get("title", "")
        time  = ev.get("occurred", "")
        svc   = f"  `{ev['service']}`" if ev.get("service") else ""
        if url:
            lines.append(f"{emoji} *<{url}|{title}>*{svc}{actor} `{time}`")
        else:
            lines.append(f"{emoji} *{title}*{svc}{actor} `{time}`")

    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*🕐 Changes Before This Incident (last 60 min)*\n" + "\n".join(lines),
            },
        },
        {"type": "divider"},
    ]


def _send_slack_brief(webhook_url: str, payload: dict) -> str | None:
    """
    Send the brief to Slack.
    Returns the message timestamp (ts) if available for threading.
    Note: Incoming webhooks don't return ts. For threading, use Slack Web API.
    Webhooks are used here for simplicity; threading requires bot token upgrade.
    """
    try:
        resp = httpx.post(webhook_url, json=payload, timeout=15)
        if resp.status_code == 200:
            return "webhook_delivered"
        logger.error("Slack brief delivery failed: %s %s", resp.status_code, resp.text)
        return None
    except Exception as exc:
        logger.error("Slack brief delivery exception: %s", exc)
        return None


def _send_status_update_slack(
    webhook_url: str,
    brief_id: str,
    title: str,
    status: str,
    update_text: str,
    updated_by: str | None,
) -> None:
    """Post a status update notification using the unified notification service."""
    try:
        from apps.api.services.notifications import (
            build_status_update_payload, send_webhook as _send_notification,
        )
        payload = build_status_update_payload(
            brief_id=brief_id,
            title=title,
            old_status="",
            new_status=status,
            updated_by=updated_by or "system",
            note=update_text,
        )
        _send_notification(webhook_url, payload)
    except Exception as exc:
        logger.warning("Status update delivery failed: %s", exc)


# ── Incident upsert ───────────────────────────────────────────────────────────
async def _upsert_incident_from_brief(
    tenant_id: str,
    brief_id: str,
    fields: dict,
    related_items: list,
    owner_team: str | None,
    detected_at: datetime,
    error_count: int,
    error_signature: str,
    timeline_events: list[dict],
) -> None:
    """
    Create (or skip if duplicate) an Incident record from a freshly generated
    RRT brief so the Incidents page stays in sync automatically.

    Deduplication: if an Incident with the same error_signature was created
    in the last 2 hours, we skip rather than create a duplicate.
    """
    try:
        import sqlalchemy as sa
        from apps.worker.db import AsyncSession
        from apps.api.db.models import Incident

        # Map error count → severity
        if error_count >= 20:
            severity = "p0"
        elif error_count >= 10:
            severity = "p1"
        elif error_count >= 5:
            severity = "p2"
        elif error_count >= 2:
            severity = "p3"
        else:
            severity = "p4"

        async with AsyncSession() as db:
            # Deduplicate: skip if same sig incident created recently
            cutoff = detected_at - timedelta(hours=2)
            dupe = await db.execute(
                sa.select(Incident.id).where(
                    Incident.tenant_id == tenant_id,
                    Incident.created_at >= cutoff,
                    Incident.title == fields.get("title", "")[:512],
                ).limit(1)
            )
            if dupe.scalar_one_or_none():
                logger.debug("Incident already exists for brief %s — skipping upsert", brief_id[:8])
                return

            # Map related items → contributing_factors and signals
            contributing = [
                f"[{r['source_type'].upper()}] {r['title']}"
                for r in related_items[:5]
            ]
            signals = [
                {
                    "type": r["source_type"],
                    "title": r["title"],
                    "url": r.get("url", ""),
                    "snippet": r.get("snippet", "")[:200],
                    "score": r.get("score", 0),
                }
                for r in related_items[:10]
            ]
            # Link back to the RRT brief
            signals.append({"type": "rrt_brief", "brief_id": brief_id})

            # Map timeline events to Incident timeline format
            timeline = [
                {
                    "time": ev.get("occurred", ""),
                    "type": ev.get("type", "event"),
                    "title": ev.get("title", ""),
                    "actor": ev.get("actor", ""),
                    "service": ev.get("service", ""),
                    "url": ev.get("url", ""),
                }
                for ev in timeline_events[:15]
            ]

            incident = Incident(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                title=fields.get("title", "Untitled Incident")[:512],
                description=fields.get("what_happened", ""),
                status="open",
                severity=severity,
                service=owner_team,
                started_at=detected_at,
                timeline=timeline,
                root_cause=fields.get("suspected_cause", ""),
                contributing_factors=contributing,
                recommendations=fields.get("next_actions", []),
                signals=signals,
            )
            db.add(incident)
            await db.commit()
            logger.info(
                "Incident created from RRT brief %s: title='%s' severity=%s tenant=%s",
                brief_id[:8], incident.title[:60], severity, tenant_id,
            )
    except Exception as exc:
        logger.warning("Failed to create Incident from RRT brief %s (non-fatal): %s", brief_id[:8], exc)


# ── DB persistence ────────────────────────────────────────────────────────────
async def _save_rrt_brief(
    tenant_id: str,
    brief_id: str,
    fields: dict,
    related_items: list,
    owner_team: str | None,
    owner_contacts: list[str],
    error_signature: str,
    error_sample: str,
    detected_at: datetime,
    channels_sent: list[str],
    slack_ts: str | None,
    slack_channel: str | None,
    code_frames: list[dict] | None = None,
) -> None:
    try:
        from apps.worker.db import AsyncSession
        from apps.api.models.rrt_brief import RRTBrief
        async with AsyncSession() as db:
            brief = RRTBrief(
                id=brief_id,
                tenant_id=tenant_id,
                title=fields.get("title", "Untitled Incident"),
                what_happened=fields.get("what_happened", ""),
                impact=fields.get("impact", ""),
                detected_at=detected_at,
                suspected_cause=fields.get("suspected_cause", ""),
                next_actions=fields.get("next_actions", []),
                related_items=related_items,
                owner_team=owner_team,
                owner_contacts=owner_contacts,
                status="open",
                error_signature=error_signature,
                error_sample=error_sample,
                channels_sent=channels_sent,
                slack_ts=slack_ts,
                slack_channel=slack_channel,
                code_frames=code_frames or [],
            )
            db.add(brief)
            await db.commit()
            logger.info("RRT brief %s saved for tenant=%s", brief_id, tenant_id)
    except Exception as exc:
        logger.warning("Failed to save RRT brief: %s", exc)


# ── Main Celery task ──────────────────────────────────────────────────────────
@shared_task(
    name="rrt.generate_brief",
    bind=True,
    max_retries=1,
    soft_time_limit=180,
    time_limit=210,
)
def generate_rrt_brief(
    self,
    tenant_id: str,
    error_group_dict: dict,
    related_items: list[dict],
    routing_targets: list[dict],
    error_count: int,
    window_minutes: int,
):
    """
    Generate a structured RRT brief from enrichment results and deliver to Slack.

    Args:
        tenant_id:       tenant for DB scoping
        error_group_dict: serialised ErrorGroup (signature, first_line, sample_lines)
        related_items:   RAG results from Qdrant (list of RelatedItem dicts)
        routing_targets: list of {team_name, slack_webhook, email_recipients}
        error_count:     how many times the error occurred
        window_minutes:  scanning window (for display)
    """
    try:
        brief_id = str(uuid.uuid4())
        detected_at = datetime.now(tz=timezone.utc)

        error_first_line = error_group_dict.get("first_line", "")
        error_sample = error_group_dict.get("sample_lines", [])
        error_signature = error_group_dict.get("signature", "")

        # Primary owner = first routing target
        owner_team = routing_targets[0]["team_name"] if routing_targets else None
        owner_contacts = routing_targets[0].get("email_recipients", []) if routing_targets else []

        logger.info(
            "Generating RRT brief %s for tenant=%s sig=%s owner=%s",
            brief_id[:8], tenant_id, error_signature, owner_team,
        )

        # 1a. Fetch timeline context: what changed in the 60 min before this incident
        timeline_events = _run_async(
            _fetch_timeline_context(tenant_id, detected_at, window_minutes=60)
        )
        if timeline_events:
            logger.info(
                "RRT brief %s: %d timeline events found before incident",
                brief_id[:8], len(timeline_events),
            )
        else:
            logger.info("RRT brief %s: no timeline events in pre-incident window", brief_id[:8])

        # 1b. Parse stack trace → fetch actual source code from GitHub
        frames = _parse_traceback_frames(error_sample)
        code_frames: list[dict] = []
        if frames:
            logger.info(
                "RRT brief %s: parsed %d app code frames from stack trace",
                brief_id[:8], len(frames),
            )
            code_frames = _run_async(
                _fetch_github_code_context(frames, tenant_id)
            )
            if code_frames:
                logger.info(
                    "RRT brief %s: fetched code context for %d/%d frames",
                    brief_id[:8], len(code_frames), len(frames),
                )
        else:
            logger.info("RRT brief %s: no parseable app frames in stack trace", brief_id[:8])

        # 1c. LLM: generate structured fields (with code + timeline context)
        fields = _generate_brief_fields(
            error_first_line=error_first_line,
            error_sample=error_sample,
            related_items=related_items,
            owner_team=owner_team,
            detected_at=detected_at,
            timeline_events=timeline_events,
            code_frames=code_frames,
        )
        logger.info("RRT brief fields generated: title='%s'", fields.get("title"))

        # 2. Build and send Slack message to each routing target
        channels_sent: list[str] = []
        slack_ts = None
        slack_channel = None

        for target in routing_targets:
            webhook_url = target.get("slack_webhook")
            if not webhook_url:
                continue

            from apps.api.services.notifications import (
                build_rrt_brief_payload, send_webhook as _send_notification,
            )
            payload = build_rrt_brief_payload(
                brief_id=brief_id,
                fields=fields,
                related_items=related_items,
                owner_team=target.get("team_name", owner_team),
                detected_at=detected_at,
                error_count=error_count,
                window_minutes=window_minutes,
                timeline_events=timeline_events,
            )
            ok = _send_notification(webhook_url, payload)
            ts = "webhook_delivered" if ok else None
            if ts:
                channels_sent.append(f"slack:{target.get('team_name', 'default')}")
                if not slack_ts:
                    slack_ts = ts
                    slack_channel = target.get("team_name")
                logger.info(
                    "RRT brief %s sent to team='%s'",
                    brief_id[:8], target.get("team_name"),
                )

        # 3. Persist RRT brief to DB
        _run_async(_save_rrt_brief(
            tenant_id=tenant_id,
            brief_id=brief_id,
            fields=fields,
            related_items=related_items,
            owner_team=owner_team,
            owner_contacts=owner_contacts,
            error_signature=error_signature,
            error_sample="\n".join(error_sample[:5]),
            detected_at=detected_at,
            channels_sent=channels_sent,
            slack_ts=slack_ts,
            slack_channel=slack_channel,
            code_frames=code_frames,
        ))

        # 4. Auto-create a linked Incident record so the Incidents page reflects
        #    every detected issue without any manual action required
        _run_async(_upsert_incident_from_brief(
            tenant_id=tenant_id,
            brief_id=brief_id,
            fields=fields,
            related_items=related_items,
            owner_team=owner_team,
            detected_at=detected_at,
            error_count=error_count,
            error_signature=error_signature,
            timeline_events=timeline_events,
        ))

        return {
            "brief_id": brief_id,
            "title": fields.get("title"),
            "channels_sent": channels_sent,
        }

    except Exception as exc:
        logger.exception("RRT brief generation failed: %s", exc)
        raise self.retry(exc=exc, countdown=60)


# ── Status update task (called from API) ─────────────────────────────────────
@shared_task(name="rrt.send_status_update")
def send_rrt_status_update(
    brief_id: str,
    title: str,
    status: str,
    update_text: str,
    webhook_url: str,
    updated_by: str | None = None,
):
    """Post a status update to Slack when a brief's status changes."""
    _send_status_update_slack(
        webhook_url=webhook_url,
        brief_id=brief_id,
        title=title,
        status=status,
        update_text=update_text,
        updated_by=updated_by,
    )
