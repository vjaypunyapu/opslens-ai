"""
OpsLens AI – Alert Engine
Evaluates alert rules against new insights and dispatches notifications.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import httpx
import sqlalchemy as sa
from celery import shared_task
from celery.utils.log import get_task_logger

from ..db import AsyncSession
from ..models.alert import AlertHistory, AlertRule
from ..models.insight import Insight

logger = get_task_logger(__name__)


# ── Rule evaluation ───────────────────────────────────────────────────────────
def _evaluate_condition(insight: Insight, condition: dict) -> bool:
    """
    Evaluate a condition dict against an insight.
    Supported operators: gt, gte, lt, lte, eq, contains
    Supported fields:    magnitude, insight_type, title
    """
    field    = condition.get("field", "magnitude")
    operator = condition.get("operator", "gt")
    value    = condition.get("value")

    actual = getattr(insight, field, None)
    if actual is None:
        return False

    ops = {
        "gt":       lambda a, v: float(a) > float(v),
        "gte":      lambda a, v: float(a) >= float(v),
        "lt":       lambda a, v: float(a) < float(v),
        "lte":      lambda a, v: float(a) <= float(v),
        "eq":       lambda a, v: str(a) == str(v),
        "contains": lambda a, v: str(v).lower() in str(a).lower(),
    }
    evaluator = ops.get(operator)
    if not evaluator:
        return False
    try:
        return evaluator(actual, value)
    except (ValueError, TypeError):
        return False


# ── Notification dispatchers ──────────────────────────────────────────────────
async def _send_slack(webhook_url: str, insight: Insight, rule: AlertRule) -> bool:
    message = {
        "text": f"*OpsLens Alert: {rule.name}*",
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"⚠️ {rule.name}"},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Insight:*\n{insight.title}"},
                    {"type": "mrkdwn", "text": f"*Type:*\n{insight.insight_type}"},
                    {"type": "mrkdwn", "text": f"*Summary:*\n{insight.summary}"},
                    {"type": "mrkdwn", "text": f"*Magnitude:*\n{insight.magnitude}"},
                ],
            },
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"Detected at {insight.generated_at:%Y-%m-%d %H:%M UTC}"}
                ],
            },
        ],
    }
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(webhook_url, json=message)
        return resp.status_code == 200


async def _send_email(recipients: list[str], insight: Insight, rule: AlertRule) -> bool:
    """
    Send via SendGrid Transactional Email API.
    Requires SENDGRID_API_KEY in settings.
    """
    from ..config import settings

    payload = {
        "personalizations": [{"to": [{"email": e} for e in recipients]}],
        "from": {"email": "alerts@opslens.ai", "name": "OpsLens Alerts"},
        "subject": f"[OpsLens Alert] {rule.name}: {insight.title}",
        "content": [
            {
                "type": "text/plain",
                "value": (
                    f"Alert: {rule.name}\n\n"
                    f"Insight: {insight.title}\n"
                    f"Type: {insight.insight_type}\n"
                    f"Summary: {insight.summary}\n"
                    f"Magnitude: {insight.magnitude}\n\n"
                    f"Detected at {insight.generated_at:%Y-%m-%d %H:%M UTC}"
                ),
            }
        ],
    }
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            "https://api.sendgrid.com/v3/mail/send",
            json=payload,
            headers={"Authorization": f"Bearer {settings.SENDGRID_API_KEY}"},
        )
        return resp.status_code == 202


# ── Core evaluation loop ──────────────────────────────────────────────────────
@shared_task(name="alerts.evaluate_for_tenant", bind=True, max_retries=2)
def evaluate_alerts_for_tenant(self, tenant_id: str):
    import asyncio
    try:
        asyncio.run(_evaluate_alerts_async(tenant_id))
    except Exception as exc:
        raise self.retry(exc=exc) from exc


async def _evaluate_alerts_async(tenant_id: str):
    now = datetime.now(tz=timezone.utc)

    async with AsyncSession() as db:
        # Fetch all enabled alert rules for this tenant
        rules_result = await db.execute(
            sa.select(AlertRule).where(
                AlertRule.tenant_id == tenant_id,
                AlertRule.enabled,
            )
        )
        rules: list[AlertRule] = rules_result.scalars().all()

        if not rules:
            return

        # Fetch insights generated in the last 2 hours (since last run)
        insights_result = await db.execute(
            sa.select(Insight).where(
                Insight.tenant_id == tenant_id,
                Insight.status == "active",
                Insight.generated_at >= now - timedelta(hours=2),
            )
        )
        insights: list[Insight] = insights_result.scalars().all()

        for rule in rules:
            # Check cooldown
            if rule.last_fired_at and (now - rule.last_fired_at).total_seconds() < rule.cooldown_hours * 3600:
                continue

            for insight in insights:
                # Check insight type filter
                if rule.insight_types and insight.insight_type not in rule.insight_types:
                    continue

                # Evaluate condition
                if not _evaluate_condition(insight, rule.condition):
                    continue

                # Fire the alert
                channels_sent = []
                config = rule.channel_config or {}

                if "slack" in (rule.channels or []):
                    webhook = config.get("slack_webhook_url")
                    if webhook:
                        success = await _send_slack(webhook, insight, rule)
                        if success:
                            channels_sent.append("slack")

                if "email" in (rule.channels or []):
                    recipients = config.get("email_recipients", [])
                    if recipients:
                        success = await _send_email(recipients, insight, rule)
                        if success:
                            channels_sent.append("email")

                if channels_sent:
                    # Record the dispatch
                    history = AlertHistory(
                        id=str(uuid.uuid4()),
                        tenant_id=tenant_id,
                        rule_id=rule.id,
                        insight_id=insight.id,
                        channels_sent=channels_sent,
                        payload={"insight_title": insight.title, "rule_name": rule.name},
                        fired_at=now,
                        delivery_status="sent",
                    )
                    db.add(history)
                    rule.last_fired_at = now
                    logger.info("[%s] Fired alert '%s' via %s", tenant_id, rule.name, channels_sent)

                    # Only fire once per rule per cycle
                    break

        await db.commit()
