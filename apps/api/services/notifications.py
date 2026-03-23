"""
OpsLens AI — Unified Notification Service
==========================================
Single module that handles sending alerts to Slack OR Microsoft Teams.

Set NOTIFICATION_PROVIDER=slack|teams in .env.
For Teams, also set TEAMS_WEBHOOK_TYPE=connector|workflow
  - connector  → legacy Office 365 MessageCard format (most common, default)
  - workflow   → modern Power Automate AdaptiveCard format

Usage (from Celery tasks):
    from apps.api.services.notifications import send_webhook, build_fast_alert_payload
    payload = build_fast_alert_payload(eg, "default", 5, "Payments")
    send_webhook(webhook_url, payload)

All build_* functions automatically use the provider from settings.
Pass provider="slack"|"teams" to override per-call.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Literal

import httpx

from apps.api.config import settings

logger = logging.getLogger(__name__)

Provider = Literal["slack", "teams"]


# ── Transport ─────────────────────────────────────────────────────────────────

def send_webhook(url: str, payload: dict, timeout: int = 10) -> bool:
    """
    POST a notification payload to a webhook URL.
    Returns True on success. Never raises — logs the error instead.
    """
    try:
        resp = httpx.post(url, json=payload, timeout=timeout)
        if resp.status_code not in (200, 202, 204):
            logger.warning(
                "Webhook returned %s: %s",
                resp.status_code,
                resp.text[:200],
            )
            return False
        return True
    except Exception as exc:
        logger.error("Webhook delivery failed: %s", exc)
        return False


def _provider() -> Provider:
    """Return the configured notification provider."""
    p = getattr(settings, "NOTIFICATION_PROVIDER", "slack").lower()
    return "teams" if p == "teams" else "slack"


# ════════════════════════════════════════════════════════════════════════════════
# FAST ALERT — raw exception spike notification
# ════════════════════════════════════════════════════════════════════════════════

def build_fast_alert_payload(
    error_lines: list[str],
    error_count: int,
    window_minutes: int,
    team_name: str,
    tenant_id: str,
    provider: Provider | None = None,
) -> dict:
    """Build the immediate raw alert payload (no LLM, fires in < 2 min)."""
    p = provider or _provider()
    preview = "\n".join(error_lines[:3])[:500]
    now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    if p == "teams":
        return _teams_fast_alert(
            preview, error_count, window_minutes, team_name, tenant_id, now
        )
    return _slack_fast_alert(
        preview, error_count, window_minutes, team_name, tenant_id, now
    )


def _slack_fast_alert(preview, count, window, team, tenant, now) -> dict:
    return {
        "text": f"🚨 Exception Alert — {team} ({count}x in {window}m)",
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"🚨 Exception Alert — {team}"},
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Occurred:* {count}x in last {window} min  •  *{now}*\n"
                        f"*Error:*\n```{preview}```"
                    ),
                },
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": "⏳ _Searching Jira, Slack & GitHub for related context… enriched report incoming._",
                    }
                ],
            },
        ],
    }


def _teams_fast_alert(preview, count, window, team, tenant, now) -> dict:
    webhook_type = getattr(settings, "TEAMS_WEBHOOK_TYPE", "connector")
    if webhook_type == "workflow":
        return {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": {
                        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                        "type": "AdaptiveCard",
                        "version": "1.4",
                        "body": [
                            {
                                "type": "TextBlock",
                                "text": f"🚨 Exception Alert — {team}",
                                "weight": "Bolder",
                                "size": "Large",
                                "color": "Attention",
                            },
                            {
                                "type": "FactSet",
                                "facts": [
                                    {"title": "Occurrences", "value": f"{count}x in last {window} min"},
                                    {"title": "Time", "value": now},
                                    {"title": "Team", "value": team},
                                    {"title": "Tenant", "value": tenant},
                                ],
                            },
                            {
                                "type": "TextBlock",
                                "text": "**Error:**",
                                "weight": "Bolder",
                            },
                            {
                                "type": "TextBlock",
                                "text": preview,
                                "fontType": "Monospace",
                                "wrap": True,
                                "color": "Attention",
                            },
                            {
                                "type": "TextBlock",
                                "text": "⏳ Searching Jira, Slack & GitHub for context… enriched report incoming.",
                                "isSubtle": True,
                                "italic": True,
                            },
                        ],
                        "msteams": {"width": "Full"},
                    },
                }
            ],
        }
    # Default: Office 365 Connector MessageCard
    return {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "themeColor": "FF0000",
        "summary": f"🚨 Exception Alert — {team}",
        "sections": [
            {
                "activityTitle": f"🚨 Exception Alert — {team}",
                "activitySubtitle": f"{count}x in last {window} min  ·  {now}",
                "facts": [
                    {"name": "Team", "value": team},
                    {"name": "Tenant", "value": tenant},
                    {"name": "Occurrences", "value": f"{count}x in {window} min"},
                ],
                "text": f"```\n{preview}\n```",
            },
            {
                "text": "⏳ _Searching Jira, Slack & GitHub for context… enriched report incoming._",
            },
        ],
    }


# ════════════════════════════════════════════════════════════════════════════════
# ENRICHED ALERT — RAG + LLM diagnosis follow-up
# ════════════════════════════════════════════════════════════════════════════════

def build_enriched_alert_payload(
    error_first_line: str,
    error_preview: str,
    error_count: int,
    window_minutes: int,
    diagnosis: str,
    related_docs: list[dict],
    tenant_id: str,
    provider: Provider | None = None,
) -> dict:
    """Build the enriched follow-up alert with RAG context and LLM diagnosis."""
    p = provider or _provider()
    now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    if p == "teams":
        return _teams_enriched_alert(
            error_first_line, error_preview, error_count,
            window_minutes, diagnosis, related_docs, tenant_id, now
        )
    return _slack_enriched_alert(
        error_first_line, error_preview, error_count,
        window_minutes, diagnosis, related_docs, tenant_id, now
    )


def _slack_enriched_alert(
    first_line, preview, count, window, diagnosis, related_docs, tenant_id, now
) -> dict:
    by_source: dict[str, list[dict]] = {}
    for d in related_docs:
        by_source.setdefault(d["source_type"], []).append(d)

    source_emojis = {"jira": "🎫", "slack": "💬", "github": "🐙"}

    blocks: list[dict] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"🚨 Exception Detected — {tenant_id}"},
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"*{now}*  •  Occurred *{count}x* in last {window} min",
                }
            ],
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Exception:*\n```{preview[:600]}```",
            },
        },
    ]

    if diagnosis:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*🤖 AI Diagnosis:*\n{diagnosis}"},
        })

    blocks.append({"type": "divider"})

    if related_docs:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*📎 Related context:*"},
        })
        for stype, docs in by_source.items():
            emoji = source_emojis.get(stype, "📄")
            lines = []
            for d in docs[:3]:
                url, title = d.get("url", ""), d.get("title", "Untitled")
                snippet = d.get("snippet", "")[:120].replace("\n", " ")
                lines.append(
                    f"• *<{url}|{title}>*\n  _{snippet}_" if url
                    else f"• *{title}*\n  _{snippet}_"
                )
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"{emoji} *{stype.upper()}*\n" + "\n".join(lines)},
            })
    else:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "_No related context found. Sync integrations to enable enrichment._",
            },
        })

    blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": "Powered by *OpsLens AI*"}],
    })

    return {
        "text": f"🚨 Exception in {tenant_id}: {first_line[:80]}",
        "blocks": blocks,
    }


def _teams_enriched_alert(
    first_line, preview, count, window, diagnosis, related_docs, tenant_id, now
) -> dict:
    webhook_type = getattr(settings, "TEAMS_WEBHOOK_TYPE", "connector")

    related_facts = []
    for d in related_docs[:5]:
        url  = d.get("url", "")
        title = d.get("title", "Untitled")
        stype = d.get("source_type", "").upper()
        link  = f"[{title}]({url})" if url else title
        related_facts.append({"name": stype, "value": link})

    if webhook_type == "workflow":
        body: list[dict] = [
            {
                "type": "TextBlock",
                "text": f"🚨 Exception Detected — {tenant_id}",
                "weight": "Bolder",
                "size": "Large",
                "color": "Attention",
            },
            {
                "type": "FactSet",
                "facts": [
                    {"title": "Time", "value": now},
                    {"title": "Occurrences", "value": f"{count}x in {window} min"},
                ],
            },
            {"type": "TextBlock", "text": "**Exception:**", "weight": "Bolder"},
            {"type": "TextBlock", "text": preview[:600], "fontType": "Monospace", "wrap": True},
        ]
        if diagnosis:
            body.extend([
                {"type": "TextBlock", "text": "**🤖 AI Diagnosis:**", "weight": "Bolder"},
                {"type": "TextBlock", "text": diagnosis, "wrap": True},
            ])
        if related_facts:
            body.append({"type": "TextBlock", "text": "**📎 Related Context:**", "weight": "Bolder"})
            body.append({"type": "FactSet", "facts": related_facts})

        return {
            "type": "message",
            "attachments": [{
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": body,
                    "msteams": {"width": "Full"},
                },
            }],
        }

    # Connector MessageCard
    sections: list[dict] = [
        {
            "activityTitle": f"🚨 Exception Detected — {tenant_id}",
            "activitySubtitle": f"{count}x in last {window} min  ·  {now}",
            "text": f"```\n{preview[:500]}\n```",
        }
    ]
    if diagnosis:
        sections.append({"title": "🤖 AI Diagnosis", "text": diagnosis})
    if related_facts:
        sections.append({"title": "📎 Related Context", "facts": related_facts})

    return {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "themeColor": "FF4500",
        "summary": f"Exception in {tenant_id}: {first_line[:80]}",
        "sections": sections,
    }


# ════════════════════════════════════════════════════════════════════════════════
# RRT BRIEF
# ════════════════════════════════════════════════════════════════════════════════

def build_rrt_brief_payload(
    brief_id: str,
    fields: dict,
    related_items: list[dict],
    owner_team: str | None,
    detected_at: datetime,
    error_count: int,
    window_minutes: int,
    timeline_events: list[dict] | None = None,
    provider: Provider | None = None,
) -> dict:
    """Build the full RRT Brief notification payload."""
    p = provider or _provider()
    if p == "teams":
        return _teams_rrt_brief(
            brief_id, fields, related_items, owner_team,
            detected_at, error_count, window_minutes, timeline_events or []
        )
    return _slack_rrt_brief(
        brief_id, fields, related_items, owner_team,
        detected_at, error_count, window_minutes, timeline_events or []
    )


def _slack_rrt_brief(
    brief_id, fields, related_items, owner_team,
    detected_at, error_count, window_minutes, timeline_events
) -> dict:
    """Full Slack Block Kit RRT brief — imported from rrt_briefing.py logic."""
    source_emojis = {"jira": "🎫", "github": "🐙", "slack": "💬"}

    related_lines = []
    for item in related_items[:6]:
        emoji = source_emojis.get(item.get("source_type", ""), "📄")
        url   = item.get("url", "")
        title = item.get("title", "Untitled")
        snip  = item.get("snippet", "")[:100].replace("\n", " ")
        related_lines.append(
            f"{emoji} *<{url}|{title}>*\n  _{snip}_" if url
            else f"{emoji} *{title}*\n  _{snip}_"
        )

    related_text = "\n".join(related_lines) or \
        "_No related tickets, PRs, or threads found. Sync integrations to enable this._"

    actions = fields.get("next_actions", [])
    actions_text = "\n".join(f"{i+1}. {a}" for i, a in enumerate(actions[:5]))

    # Timeline section
    timeline_blocks: list[dict] = []
    if timeline_events:
        type_labels = {
            "github_deploy": "🚀 Deploy",
            "github_pr": "🔀 PR",
            "github_commit": "📝 Commit",
            "jira_issue": "🎫 Jira",
            "manual": "📌 Manual",
        }
        lines = []
        for ev in timeline_events[:8]:
            label = type_labels.get(ev.get("type", ""), "📌")
            svc = f" ({ev['service']})" if ev.get("service") else ""
            actor = f" — {ev['actor']}" if ev.get("actor") else ""
            lines.append(f"{label} *{ev.get('title', '')}*{svc}{actor} at {ev.get('occurred', '')}")
        timeline_blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*🕐 Changes Before This Incident (last 60 min)*\n" + "\n".join(lines),
                },
            },
            {"type": "divider"},
        ]
    else:
        timeline_blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*🕐 Changes Before This Incident*\n_No GitHub / Jira events recorded. Connect webhooks to enable change correlation._",
                },
            },
            {"type": "divider"},
        ]

    return {
        "text": f"🔴 [OPEN] Incident Brief — {fields.get('title', 'Exception Detected')}",
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"🔴 [OPEN] Incident Brief — {fields.get('title', 'Exception Detected')}",
                },
            },
            {
                "type": "context",
                "elements": [{
                    "type": "mrkdwn",
                    "text": (
                        f"Detected *{detected_at.strftime('%Y-%m-%d %H:%M UTC')}*  •  "
                        f"Occurred *{error_count}x* in last {window_minutes} min  •  "
                        f"Owner: *{owner_team or 'Unassigned'}*  •  "
                        f"Brief ID: `{brief_id[:8]}`"
                    ),
                }],
            },
            {"type": "divider"},
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*📋 What Happened*\n{fields.get('what_happened', 'Under investigation.')}"},
                    {"type": "mrkdwn", "text": f"*⚡ Impact*\n{fields.get('impact', 'Under investigation.')}"},
                ],
            },
            {"type": "divider"},
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*🔍 Suspected Cause*\n{fields.get('suspected_cause', 'Under investigation.')}"},
            },
            {"type": "divider"},
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*📎 Related Context*\n{related_text}"},
            },
            {"type": "divider"},
            *timeline_blocks,
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*✅ Next Actions*\n{actions_text}"},
            },
            {"type": "divider"},
            {
                "type": "context",
                "elements": [{
                    "type": "mrkdwn",
                    "text": f"Brief `{brief_id[:8]}` · Update status: `PATCH /api/v1/rrt-briefs/{brief_id}` · Powered by *OpsLens AI*",
                }],
            },
        ],
    }


def _teams_rrt_brief(
    brief_id, fields, related_items, owner_team,
    detected_at, error_count, window_minutes, timeline_events
) -> dict:
    webhook_type = getattr(settings, "TEAMS_WEBHOOK_TYPE", "connector")
    title = fields.get("title", "Exception Detected")
    actions = fields.get("next_actions", [])
    actions_text = "\n".join(f"{i+1}. {a}" for i, a in enumerate(actions[:5]))

    related_facts = [
        {"name": item.get("source_type", "").upper(), "value": f"[{item.get('title','?')}]({item.get('url','')})" if item.get("url") else item.get("title", "?")}
        for item in related_items[:6]
    ]

    timeline_text = ""
    if timeline_events:
        lines = []
        type_icons = {"github_deploy": "🚀", "github_pr": "🔀", "jira_issue": "🎫"}
        for ev in timeline_events[:5]:
            icon = type_icons.get(ev.get("type", ""), "📌")
            lines.append(f"{icon} {ev.get('title','')} at {ev.get('occurred','')}")
        timeline_text = "\n".join(lines)
    else:
        timeline_text = "No pre-incident changes recorded."

    if webhook_type == "workflow":
        body = [
            {"type": "TextBlock", "text": f"🔴 Incident Brief — {title}", "weight": "Bolder", "size": "Large", "color": "Attention"},
            {"type": "FactSet", "facts": [
                {"title": "Detected", "value": detected_at.strftime("%Y-%m-%d %H:%M UTC")},
                {"title": "Occurrences", "value": f"{error_count}x in {window_minutes} min"},
                {"title": "Owner Team", "value": owner_team or "Unassigned"},
                {"title": "Brief ID", "value": brief_id[:8]},
            ]},
            {"type": "TextBlock", "text": "**📋 What Happened**", "weight": "Bolder"},
            {"type": "TextBlock", "text": fields.get("what_happened", "Under investigation."), "wrap": True},
            {"type": "TextBlock", "text": "**⚡ Impact**", "weight": "Bolder"},
            {"type": "TextBlock", "text": fields.get("impact", "Under investigation."), "wrap": True},
            {"type": "TextBlock", "text": "**🔍 Suspected Cause**", "weight": "Bolder"},
            {"type": "TextBlock", "text": fields.get("suspected_cause", "Under investigation."), "wrap": True},
            {"type": "TextBlock", "text": "**🕐 Changes Before Incident**", "weight": "Bolder"},
            {"type": "TextBlock", "text": timeline_text, "wrap": True},
            {"type": "TextBlock", "text": "**✅ Next Actions**", "weight": "Bolder"},
            {"type": "TextBlock", "text": actions_text, "wrap": True},
        ]
        if related_facts:
            body.extend([
                {"type": "TextBlock", "text": "**📎 Related Context**", "weight": "Bolder"},
                {"type": "FactSet", "facts": related_facts},
            ])
        return {
            "type": "message",
            "attachments": [{
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": body,
                    "msteams": {"width": "Full"},
                },
            }],
        }

    # Connector MessageCard
    sections: list[dict] = [
        {
            "activityTitle": f"🔴 Incident Brief — {title}",
            "activitySubtitle": (
                f"{detected_at.strftime('%Y-%m-%d %H:%M UTC')}  ·  "
                f"{error_count}x in {window_minutes} min  ·  "
                f"Owner: {owner_team or 'Unassigned'}  ·  ID: {brief_id[:8]}"
            ),
            "facts": [
                {"name": "What Happened", "value": fields.get("what_happened", "Under investigation.")},
                {"name": "Impact", "value": fields.get("impact", "Under investigation.")},
                {"name": "Suspected Cause", "value": fields.get("suspected_cause", "Under investigation.")},
            ],
        },
        {"title": "🕐 Changes Before Incident", "text": timeline_text or "_None recorded_"},
        {"title": "✅ Next Actions", "text": actions_text},
    ]
    if related_facts:
        sections.append({"title": "📎 Related Context", "facts": related_facts})

    return {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "themeColor": "CC0000",
        "summary": f"Incident Brief — {title}",
        "sections": sections,
    }


# ════════════════════════════════════════════════════════════════════════════════
# HOURLY DIGEST
# ════════════════════════════════════════════════════════════════════════════════

def build_digest_payload(
    summary: str,
    issue_groups: list[dict],
    scan_duration_s: float,
    tenant_id: str,
    window_minutes: int,
    provider: Provider | None = None,
) -> dict:
    """Build the hourly LLM digest notification payload."""
    p = provider or _provider()
    now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    if p == "teams":
        return _teams_digest(summary, issue_groups, scan_duration_s, tenant_id, window_minutes, now)
    return _slack_digest(summary, issue_groups, scan_duration_s, tenant_id, window_minutes, now)


def _slack_digest(summary, issues, duration, tenant_id, window, now) -> dict:
    issue_lines = "\n".join(
        f"• *{g.get('signature','?')}* — {g.get('count',1)}x — _{g.get('first_line','')[:100]}_"
        for g in issues[:10]
    )
    return {
        "text": f"📊 Hourly Log Digest — {tenant_id} ({len(issues)} issues)",
        "blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": f"📊 Hourly Log Digest — {tenant_id}"}},
            {"type": "context", "elements": [{"type": "mrkdwn", "text": f"*{now}*  •  Last {window} min  •  {len(issues)} issue groups  •  Scan: {duration:.1f}s"}]},
            {"type": "divider"},
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*🤖 AI Summary*\n{summary}"}},
            {"type": "divider"},
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*Top Issues*\n{issue_lines or '_No issues detected_'}"}},
            {"type": "context", "elements": [{"type": "mrkdwn", "text": "Powered by *OpsLens AI*"}]},
        ],
    }


def _teams_digest(summary, issues, duration, tenant_id, window, now) -> dict:
    webhook_type = getattr(settings, "TEAMS_WEBHOOK_TYPE", "connector")
    issue_facts = [
        {"name": g.get("signature", "?")[:30], "value": f"{g.get('count',1)}x — {g.get('first_line','')[:80]}"}
        for g in issues[:8]
    ]

    if webhook_type == "workflow":
        body = [
            {"type": "TextBlock", "text": f"📊 Hourly Log Digest — {tenant_id}", "weight": "Bolder", "size": "Large"},
            {"type": "FactSet", "facts": [
                {"title": "Time", "value": now},
                {"title": "Window", "value": f"Last {window} min"},
                {"title": "Issues Found", "value": str(len(issues))},
            ]},
            {"type": "TextBlock", "text": "**🤖 AI Summary**", "weight": "Bolder"},
            {"type": "TextBlock", "text": summary, "wrap": True},
        ]
        if issue_facts:
            body.extend([
                {"type": "TextBlock", "text": "**Top Issues**", "weight": "Bolder"},
                {"type": "FactSet", "facts": issue_facts},
            ])
        return {
            "type": "message",
            "attachments": [{
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": body,
                    "msteams": {"width": "Full"},
                },
            }],
        }

    return {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "themeColor": "0078D4",
        "summary": f"Hourly Log Digest — {tenant_id} ({len(issues)} issues)",
        "sections": [
            {
                "activityTitle": f"📊 Hourly Log Digest — {tenant_id}",
                "activitySubtitle": f"{now}  ·  Last {window} min  ·  {len(issues)} issues",
                "text": summary,
            },
            *([ {"title": "Top Issues", "facts": issue_facts} ] if issue_facts else []),
        ],
    }


# ════════════════════════════════════════════════════════════════════════════════
# STATUS UPDATE (RRT Brief lifecycle — open → investigating → resolved)
# ════════════════════════════════════════════════════════════════════════════════

def build_status_update_payload(
    brief_id: str,
    title: str,
    old_status: str,
    new_status: str,
    updated_by: str,
    note: str | None = None,
    provider: Provider | None = None,
) -> dict:
    p = provider or _provider()
    emoji_map = {"open": "🔴", "investigating": "🟡", "resolved": "🟢"}
    emoji = emoji_map.get(new_status, "⚪")
    now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    if p == "teams":
        color_map = {"open": "Attention", "investigating": "Warning", "resolved": "Good"}
        color = color_map.get(new_status, "Default")
        webhook_type = getattr(settings, "TEAMS_WEBHOOK_TYPE", "connector")
        body = [
            {"type": "TextBlock", "text": f"{emoji} Incident Status Update", "weight": "Bolder", "size": "Large", "color": color},
            {"type": "TextBlock", "text": f"**{title}**", "weight": "Bolder"},
            {"type": "FactSet", "facts": [
                {"title": "Status", "value": f"{old_status} → **{new_status}**"},
                {"title": "Updated By", "value": updated_by},
                {"title": "Time", "value": now},
            ]},
        ]
        if note:
            body.append({"type": "TextBlock", "text": f"**Note:** {note}", "wrap": True})

        if webhook_type == "workflow":
            return {
                "type": "message",
                "attachments": [{
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": {
                        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                        "type": "AdaptiveCard",
                        "version": "1.4",
                        "body": body,
                        "msteams": {"width": "Full"},
                    },
                }],
            }

        tc = {"open": "FF0000", "investigating": "FFA500", "resolved": "00AA00"}.get(new_status, "888888")
        return {
            "@type": "MessageCard",
            "@context": "http://schema.org/extensions",
            "themeColor": tc,
            "summary": f"{emoji} Incident {new_status.upper()} — {title}",
            "sections": [{
                "activityTitle": f"{emoji} Incident Brief Status Update",
                "activitySubtitle": f"{old_status} → {new_status}  ·  {now}",
                "facts": [
                    {"name": "Incident", "value": title},
                    {"name": "Updated By", "value": updated_by},
                    *([ {"name": "Note", "value": note} ] if note else []),
                ],
            }],
        }

    # Slack
    text = f"{emoji} *Incident Brief* status changed: `{old_status}` → *{new_status}*"
    blocks: list[dict] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"{emoji} *Status Update* — {title}\n{text}"}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": f"Updated by *{updated_by}*  ·  {now}  ·  Brief `{brief_id[:8]}`"}]},
    ]
    if note:
        blocks.insert(1, {"type": "section", "text": {"type": "mrkdwn", "text": f"*Note:* {note}"}})

    return {"text": f"{emoji} {title} — {new_status}", "blocks": blocks}
