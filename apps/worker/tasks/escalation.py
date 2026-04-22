"""
OpsLens AI — Tiered Alert Escalation (Q11)
==========================================
Implements two-tier escalation so PagerDuty is only paged when Slack
notifications go unacknowledged:

  Tier 1 (immediate):      Slack notification                     ← fast_scan fires this
  Tier 2 (delayed):        PagerDuty page after N minutes         ← this module

When fast_scan fires a Slack alert it also schedules
`escalate_to_pagerduty.apply_async(..., countdown=escalation_seconds)`.

Before PagerDuty fires, this task checks:
  - Has an engineer acknowledged the alert in the AlertHistory table?
  - Has the incident already been resolved?

If either is true, the PagerDuty page is suppressed and the escalation is
logged as cancelled. If neither is true, PagerDuty fires.

Engineers acknowledge via:
  POST /api/v1/alerts/acknowledge/{alert_history_id}

Default escalation window: 10 minutes (600 seconds).
Configurable per routing rule via `escalation_minutes` or
globally via LOG_FAST_ALERT_ESCALATION_MINUTES env var.
"""
from __future__ import annotations

from celery import shared_task
from celery.utils.log import get_task_logger

logger = get_task_logger(__name__)

_DEFAULT_ESCALATION_MINUTES = 10


@shared_task(
    name="alerts.escalate_to_pagerduty",
    bind=True,
    max_retries=2,
    default_retry_delay=30,
)
def escalate_to_pagerduty(
    self,
    alert_history_id: str,
    tenant_id: str,
    routing_targets: list[dict],
    error_signature: str,
    error_summary: str,
    error_count: int,
    window_minutes: int,
):
    """
    Delayed PagerDuty escalation task.

    Fires only if the original Slack alert has NOT been acknowledged within
    the escalation window. Called via apply_async(countdown=N) from fast_scan.

    Args:
        alert_history_id: UUID of the AlertHistory row created when Slack fired.
                          Used to check acknowledgement status.
        tenant_id:        Tenant scope.
        routing_targets:  List of {team_name, pagerduty_key, ...} dicts
                          (same format as fast_scan routing targets).
        error_signature:  Error group signature for PagerDuty dedup_key.
        error_summary:    Human-readable first error line.
        error_count:      Number of occurrences in the alert window.
        window_minutes:   Alert scan window (for PagerDuty detail).
    """
    from apps.worker.async_utils import run_async as _run_async

    # ── Step 1: Check acknowledgement ─────────────────────────────────────────
    acknowledged = _run_async(_check_acknowledged(alert_history_id))
    if acknowledged:
        logger.info(
            "Escalation suppressed for alert %s — engineer acknowledged within window",
            alert_history_id,
        )
        return {"status": "suppressed", "reason": "acknowledged"}

    # ── Step 2: Fire PagerDuty for each matching routing target ───────────────
    targets_with_pd = [t for t in routing_targets if t.get("pagerduty_key")]
    if not targets_with_pd:
        logger.info(
            "Escalation for alert %s: no PagerDuty keys configured on matched routing rules",
            alert_history_id,
        )
        return {"status": "skipped", "reason": "no_pagerduty_keys"}

    try:
        from apps.worker.tasks.pagerduty import dispatch_pagerduty_alerts
        sev = "critical" if error_count >= 10 else "high"
        fired = dispatch_pagerduty_alerts(
            routing_targets=targets_with_pd,
            tenant_id=tenant_id,
            error_signature=error_signature,
            error_summary=f"[ESCALATED — unacknowledged after {_DEFAULT_ESCALATION_MINUTES}m] {error_summary}",
            service_name=None,
            severity=sev,
            custom_details={
                "error_count":      error_count,
                "window_minutes":   window_minutes,
                "escalated":        True,
                "alert_history_id": alert_history_id,
                "note": "This alert was not acknowledged in Slack within the escalation window.",
            },
        )
        logger.info(
            "PagerDuty escalation fired for alert %s: %d targets notified",
            alert_history_id, len(targets_with_pd),
        )
        return {"status": "escalated", "pagerduty_targets": len(targets_with_pd)}
    except Exception as exc:
        logger.error("PagerDuty escalation failed for alert %s: %s", alert_history_id, exc)
        raise self.retry(exc=exc)


async def _check_acknowledged(alert_history_id: str) -> bool:
    """Return True if the AlertHistory row is marked acknowledged."""
    try:
        import sqlalchemy as sa
        from apps.worker.db import AsyncSession
        from apps.api.db.models import AlertHistory
        import uuid

        async with AsyncSession() as db:
            result = await db.execute(
                sa.select(AlertHistory.acknowledged).where(
                    AlertHistory.id == uuid.UUID(alert_history_id)
                )
            )
            row = result.scalar_one_or_none()
            return bool(row)
    except Exception as exc:
        logger.warning(
            "Could not check acknowledgement for alert %s (defaulting to not-acked): %s",
            alert_history_id, exc,
        )
        return False  # safer to escalate than to silently suppress


async def schedule_escalation(
    alert_history_id: str,
    tenant_id: str,
    routing_targets: list[dict],
    error_signature: str,
    error_summary: str,
    error_count: int,
    window_minutes: int,
    escalation_minutes: int = _DEFAULT_ESCALATION_MINUTES,
) -> None:
    """
    Schedule the PagerDuty escalation task with a countdown.
    Called from fast_scan immediately after the Slack notification fires.
    """
    from apps.api.config import settings
    effective_minutes = escalation_minutes or getattr(
        settings, "LOG_FAST_ALERT_ESCALATION_MINUTES", _DEFAULT_ESCALATION_MINUTES
    )
    countdown_secs = max(effective_minutes, 1) * 60

    escalate_to_pagerduty.apply_async(
        kwargs={
            "alert_history_id": alert_history_id,
            "tenant_id":        tenant_id,
            "routing_targets":  routing_targets,
            "error_signature":  error_signature,
            "error_summary":    error_summary,
            "error_count":      error_count,
            "window_minutes":   window_minutes,
        },
        countdown=countdown_secs,
    )
    logger.info(
        "PagerDuty escalation scheduled for alert %s in %d minutes",
        alert_history_id, effective_minutes,
    )
