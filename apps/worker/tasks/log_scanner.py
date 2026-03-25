"""
OpsLens AI – Log Scanner
=========================
Periodically scans application logs for errors, exceptions, and anomalies,
then uses an LLM to summarise findings and dispatches a report to the team
via Slack and/or email.

Pipeline:
    1. Collect recent log lines from configured sources:
       - Docker container logs (via Docker socket)
       - A local log file path
    2. Filter for ERROR / WARNING / CRITICAL / EXCEPTION / TRACEBACK lines
    3. Group by error signature (deduplicate)
    4. Ask the LLM to produce a concise incident digest
    5. Send the digest to configured Slack webhook and/or email recipients
    6. Store scan result in LogScanHistory table for audit / UI display

Configuration (all in .env / Settings):
    LOG_SCAN_ENABLED          = true
    LOG_SCAN_WINDOW_MINUTES   = 60       # how far back to look
    LOG_SCAN_MAX_LINES        = 2000     # cap to avoid prompt overload
    LOG_SCAN_DOCKER_CONTAINERS = api,worker   # comma-separated container names
    LOG_SCAN_FILE_PATH        =          # optional: path to a log file
    LOG_SCAN_SLACK_WEBHOOK    =          # dedicated webhook for log reports
    LOG_SCAN_EMAIL_RECIPIENTS =          # comma-separated email list
    LOG_SCAN_MIN_ISSUES       = 1        # don't send if fewer issues found
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import datetime, timedelta, timezone
from typing import TypedDict

import httpx
import sqlalchemy as sa
from celery import shared_task
from celery.utils.log import get_task_logger

from apps.api.config import settings
from apps.worker.async_utils import run_async as _run_async
from apps.worker.db import AsyncSession

logger = get_task_logger(__name__)


# ── Regex patterns for issue detection ───────────────────────────────────────
_ISSUE_PATTERNS = re.compile(
    r"(ERROR|CRITICAL|EXCEPTION|Traceback|raise\s+\w+Error|"
    r"5\d{2}\s+\w|unhandled exception|task failed|connection refused|"
    r"timeout|out of memory|killed|segfault)",
    re.IGNORECASE,
)

_TRACEBACK_START = re.compile(r"^Traceback \(most recent call last\)", re.MULTILINE)


# ── TypedDicts ────────────────────────────────────────────────────────────────
class IssueGroup(TypedDict):
    signature: str        # short hash for dedup
    first_line: str       # representative log line
    count: int            # occurrences in window
    lines: list[str]      # up to 10 lines of context


# ── Log collection ────────────────────────────────────────────────────────────
def _collect_docker_logs(container: str, since_minutes: int) -> list[str]:
    """Pull logs from a Docker container using the docker CLI."""
    try:
        result = subprocess.run(
            [
                "docker", "logs",
                "--since", f"{since_minutes}m",
                "--tail", str(settings.LOG_SCAN_MAX_LINES),
                container,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        # Docker logs go to stderr by default
        combined = result.stdout + result.stderr
        return combined.splitlines()
    except FileNotFoundError:
        logger.warning("docker CLI not found — skipping container '%s'", container)
        return []
    except subprocess.TimeoutExpired:
        logger.warning("docker logs timed out for container '%s'", container)
        return []
    except Exception as exc:
        logger.error("Failed to collect docker logs for '%s': %s", container, exc)
        return []


def _collect_file_logs(path: str, since_minutes: int) -> list[str]:
    """Read recent lines from a log file, filtered by timestamp if possible."""
    try:
        with open(path, "r", errors="replace") as f:
            lines = f.readlines()
        # Take last LOG_SCAN_MAX_LINES lines as a simple approximation
        return [l.rstrip() for l in lines[-settings.LOG_SCAN_MAX_LINES:]]
    except FileNotFoundError:
        logger.warning("Log file not found: %s", path)
        return []
    except Exception as exc:
        logger.error("Failed to read log file '%s': %s", path, exc)
        return []


def _collect_all_logs() -> list[str]:
    """Collect from all configured sources."""
    all_lines: list[str] = []
    window = settings.LOG_SCAN_WINDOW_MINUTES

    # Docker containers
    containers = [
        c.strip()
        for c in (settings.LOG_SCAN_DOCKER_CONTAINERS or "").split(",")
        if c.strip()
    ]
    for container in containers:
        lines = _collect_docker_logs(container, window)
        logger.info("Collected %d lines from container '%s'", len(lines), container)
        all_lines.extend(lines)

    # Log file
    if settings.LOG_SCAN_FILE_PATH:
        lines = _collect_file_logs(settings.LOG_SCAN_FILE_PATH, window)
        logger.info("Collected %d lines from file '%s'", len(lines), settings.LOG_SCAN_FILE_PATH)
        all_lines.extend(lines)

    return all_lines


# ── Issue extraction ──────────────────────────────────────────────────────────
def _extract_issues(lines: list[str]) -> list[IssueGroup]:
    """
    Find ERROR/EXCEPTION/Traceback blocks, group by signature (first 80 chars),
    and deduplicate. Returns up to 20 unique issue groups.
    """
    groups: dict[str, IssueGroup] = {}
    i = 0

    while i < len(lines):
        line = lines[i]

        # Grab a traceback block (multi-line)
        if _TRACEBACK_START.search(line):
            block_lines = [line]
            j = i + 1
            while j < len(lines) and (lines[j].startswith(" ") or lines[j].startswith("\t") or "Error:" in lines[j]):
                block_lines.append(lines[j])
                j += 1
            # Signature = last line of traceback (the actual exception)
            sig_text = block_lines[-1][:120]
            sig = hashlib.md5(sig_text.encode()).hexdigest()[:8]
            if sig in groups:
                groups[sig]["count"] += 1
            else:
                groups[sig] = {
                    "signature": sig,
                    "first_line": sig_text,
                    "count": 1,
                    "lines": block_lines[:15],
                }
            i = j
            continue

        # Single-line issues
        if _ISSUE_PATTERNS.search(line):
            sig_text = re.sub(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}[^\s]*", "", line)[:120]
            sig = hashlib.md5(sig_text.encode()).hexdigest()[:8]
            if sig in groups:
                groups[sig]["count"] += 1
            else:
                groups[sig] = {
                    "signature": sig,
                    "first_line": line[:300],
                    "count": 1,
                    "lines": [line],
                }
        i += 1

    # Sort by count desc, cap at 20
    return sorted(groups.values(), key=lambda g: g["count"], reverse=True)[:20]


# ── LLM summarisation ─────────────────────────────────────────────────────────
def _build_llm_prompt(issues: list[IssueGroup], window_minutes: int) -> str:
    issue_text = ""
    for idx, g in enumerate(issues, 1):
        issue_text += (
            f"\n--- Issue #{idx} (occurrences: {g['count']}) ---\n"
            + "\n".join(g["lines"][:8])
            + "\n"
        )

    return f"""\
You are an SRE assistant analyzing application log issues from the last {window_minutes} minutes.

Below are the top error/exception groups extracted from the logs.
For each issue:
1. Write a one-sentence plain-English description of what went wrong.
2. Suggest the most likely root cause (1-2 sentences).
3. Suggest a concrete next action for the on-call engineer.

Format your response as a numbered list. Be concise. Do not reproduce full stack traces.
At the end, write a one-sentence overall severity assessment (Low / Medium / High / Critical).

LOG ISSUES:
{issue_text}
"""


def _call_llm(prompt: str) -> str:
    """Synchronous LLM call — runs inside a Celery task (no event loop)."""
    import openai

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

    else:  # openai (default)
        client = openai.OpenAI(api_key=settings.OPENAI_API_KEY)
        resp = client.chat.completions.create(
            model=settings.OPENAI_CHAT_MODEL,
            temperature=0.0,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.choices[0].message.content or ""


# ── Notification dispatchers ──────────────────────────────────────────────────
def _send_slack_report(webhook_url: str, summary: str, issue_count: int, window_minutes: int) -> bool:
    """Send a formatted log digest to Slack."""
    severity_emoji = "🟢"
    if "medium" in summary.lower():
        severity_emoji = "🟡"
    if "high" in summary.lower():
        severity_emoji = "🔴"
    if "critical" in summary.lower():
        severity_emoji = "🚨"

    # Slack has a 3000 char limit per block text field
    truncated_summary = summary[:2800] + ("…" if len(summary) > 2800 else "")

    message = {
        "text": f"{severity_emoji} OpsLens Log Report — {issue_count} issue(s) in last {window_minutes}m",
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{severity_emoji} OpsLens Log Scan — {issue_count} issue group(s)",
                },
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"Scanned last *{window_minutes} minutes* • {datetime.now(tz=timezone.utc):%Y-%m-%d %H:%M UTC}",
                    }
                ],
            },
            {"type": "divider"},
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": truncated_summary},
            },
            {"type": "divider"},
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": "Powered by *OpsLens AI Log Scanner*"}],
            },
        ],
    }
    try:
        resp = httpx.post(webhook_url, json=message, timeout=10)
        return resp.status_code == 200
    except Exception as exc:
        logger.error("Slack dispatch failed: %s", exc)
        return False


def _send_email_report(
    recipients: list[str],
    summary: str,
    issue_count: int,
    window_minutes: int,
) -> bool:
    """Send log digest via SendGrid."""
    if not settings.SENDGRID_API_KEY:
        logger.warning("SENDGRID_API_KEY not set — skipping email report")
        return False

    subject = f"[OpsLens] Log Scan Report — {issue_count} issue(s) detected"
    body = (
        f"OpsLens Log Scan Report\n"
        f"{'=' * 50}\n"
        f"Scanned: last {window_minutes} minutes\n"
        f"Generated: {datetime.now(tz=timezone.utc):%Y-%m-%d %H:%M UTC}\n"
        f"Issues found: {issue_count} unique group(s)\n\n"
        f"{summary}\n\n"
        f"— OpsLens AI"
    )

    payload = {
        "personalizations": [{"to": [{"email": e} for e in recipients]}],
        "from": {"email": settings.ALERT_FROM_EMAIL, "name": "OpsLens Alerts"},
        "subject": subject,
        "content": [{"type": "text/plain", "value": body}],
    }
    try:
        resp = httpx.post(
            "https://api.sendgrid.com/v3/mail/send",
            json=payload,
            headers={"Authorization": f"Bearer {settings.SENDGRID_API_KEY}"},
            timeout=15,
        )
        return resp.status_code == 202
    except Exception as exc:
        logger.error("Email dispatch failed: %s", exc)
        return False


# ── DB persistence ────────────────────────────────────────────────────────────
async def _save_scan_result(
    tenant_id: str,
    issue_count: int,
    summary: str,
    channels_sent: list[str],
    window_minutes: int,
) -> None:
    """Persist a LogScanHistory record so the UI can display past reports."""
    import uuid
    try:
        from apps.worker.models.log_scan import LogScanHistory
        async with AsyncSession() as db:
            record = LogScanHistory(
                id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                scanned_at=datetime.now(tz=timezone.utc),
                window_minutes=window_minutes,
                issue_count=issue_count,
                summary=summary,
                channels_sent=channels_sent,
            )
            db.add(record)
            await db.commit()
    except Exception as exc:
        # Non-fatal — don't fail the task over a missing table in early dev
        logger.warning("Could not save LogScanHistory: %s", exc)


# ── Main Celery task ──────────────────────────────────────────────────────────
@shared_task(
    name="logs.scan_and_report",
    bind=True,
    max_retries=2,
    soft_time_limit=300,
    time_limit=360,
)
def scan_logs_and_report(self, tenant_id: str | None = None):
    """
    Scan application logs, summarise issues with LLM, and notify the team.

    Args:
        tenant_id: optional — used for multi-tenant DB persistence.
                   Pass None for a system-wide (non-tenant) scan.
    """
    if not settings.LOG_SCAN_ENABLED:
        logger.info("Log scanning is disabled (LOG_SCAN_ENABLED=false)")
        return {"status": "disabled"}

    try:
        logger.info("Log scan started (window=%dm)", settings.LOG_SCAN_WINDOW_MINUTES)

        # 1. Collect
        lines = _collect_all_logs()
        if not lines:
            logger.warning("No log lines collected — check LOG_SCAN_DOCKER_CONTAINERS / LOG_SCAN_FILE_PATH")
            return {"status": "no_logs", "lines": 0}

        logger.info("Collected %d total log lines", len(lines))

        # 2. Extract issues
        issues = _extract_issues(lines)
        logger.info("Extracted %d unique issue groups", len(issues))

        if len(issues) < settings.LOG_SCAN_MIN_ISSUES:
            logger.info("Issue count %d below threshold %d — no report sent",
                        len(issues), settings.LOG_SCAN_MIN_ISSUES)
            return {"status": "below_threshold", "issues": len(issues)}

        # 3. LLM summarisation
        prompt = _build_llm_prompt(issues, settings.LOG_SCAN_WINDOW_MINUTES)
        logger.info("Calling LLM (%s) for log digest", settings.LLM_PROVIDER)
        summary = _call_llm(prompt)
        logger.info("LLM digest ready (%d chars)", len(summary))

        # 4. Dispatch
        channels_sent: list[str] = []

        slack_webhook = settings.LOG_SCAN_SLACK_WEBHOOK
        if slack_webhook:
            ok = _send_slack_report(slack_webhook, summary, len(issues), settings.LOG_SCAN_WINDOW_MINUTES)
            if ok:
                channels_sent.append("slack")
                logger.info("Log report sent to Slack")
            else:
                logger.error("Slack dispatch failed for log report")

        email_list = [
            e.strip()
            for e in (settings.LOG_SCAN_EMAIL_RECIPIENTS or "").split(",")
            if e.strip()
        ]
        if email_list:
            ok = _send_email_report(email_list, summary, len(issues), settings.LOG_SCAN_WINDOW_MINUTES)
            if ok:
                channels_sent.append("email")
                logger.info("Log report sent to %d email recipient(s)", len(email_list))
            else:
                logger.error("Email dispatch failed for log report")

        # 5. Persist
        _run_async(_save_scan_result(
            tenant_id=tenant_id or "system",
            issue_count=len(issues),
            summary=summary,
            channels_sent=channels_sent,
            window_minutes=settings.LOG_SCAN_WINDOW_MINUTES,
        ))

        result = {
            "status": "ok",
            "lines_scanned": len(lines),
            "issues_found": len(issues),
            "channels_sent": channels_sent,
        }
        logger.info("Log scan complete: %s", result)
        return result

    except Exception as exc:
        logger.exception("Log scan failed: %s", exc)
        raise self.retry(exc=exc, countdown=60)
