"""
OpsLens AI — PagerDuty Integration
=====================================
Sends trigger / resolve / acknowledge events to PagerDuty Events API v2.

Usage:
    from apps.worker.tasks.pagerduty import fire_pagerduty_alert, resolve_pagerduty_alert

The routing key (integration key) comes from AlertRoutingRule.pagerduty_key.
Each call is deduped by a `dedup_key` — OpsLens uses the ErrorGroup signature
so that repeated fires for the same error cluster to one PagerDuty incident.

PagerDuty Events API v2 docs:
    https://developer.pagerduty.com/docs/ZG9jOjExMDI5NTgw-send-an-alert-event

Severity mapping:
    critical (OpsLens)  → critical  (PagerDuty)
    high                → error
    medium              → warning
    low                 → info
"""
from __future__ import annotations

import hashlib
import json
import logging
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

PAGERDUTY_EVENTS_URL = "https://events.pagerduty.com/v2/enqueue"

_SEVERITY_MAP: dict[str, str] = {
    "critical": "critical",
    "high":     "error",
    "medium":   "warning",
    "low":      "info",
}


# ── Core send function ────────────────────────────────────────────────────────

def _send_event(payload: dict) -> dict:
    """
    POST a payload to PagerDuty Events API v2.
    Returns the parsed JSON response body.
    Raises urllib.error.HTTPError on 4xx / 5xx.
    """
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        PAGERDUTY_EVENTS_URL,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        logger.error(
            "PagerDuty API error %s: %s",
            exc.code,
            body[:500],
        )
        raise


# ── Public helpers ─────────────────────────────────────────────────────────────

def fire_pagerduty_alert(
    routing_key: str,
    summary: str,
    dedup_key: str,
    severity: str = "critical",
    source: str = "OpsLens AI",
    component: str | None = None,
    group: str | None = None,
    custom_details: dict | None = None,
) -> dict | None:
    """
    Send a TRIGGER event to PagerDuty.

    Args:
        routing_key:    PagerDuty integration/routing key (from AlertRoutingRule.pagerduty_key)
        summary:        One-line description of the alert (max 1024 chars)
        dedup_key:      Stable key for deduplication — same key = same PD incident
        severity:       "critical" | "high" | "medium" | "low"
        source:         Logical origin of the alert (service name recommended)
        component:      Component within the service (optional)
        group:          Cluster/grouping label (optional)
        custom_details: Arbitrary JSON dict shown in PagerDuty incident details

    Returns:
        PagerDuty API response dict, or None on error (non-raising — logged instead).
    """
    pd_severity = _SEVERITY_MAP.get(severity.lower(), "error")

    payload: dict[str, Any] = {
        "routing_key": routing_key,
        "event_action": "trigger",
        "dedup_key": dedup_key,
        "payload": {
            "summary": summary[:1024],
            "source": source,
            "severity": pd_severity,
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "custom_details": custom_details or {},
        },
        "client": "OpsLens AI",
    }
    if component:
        payload["payload"]["component"] = component
    if group:
        payload["payload"]["group"] = group

    try:
        result = _send_event(payload)
        logger.info(
            "PagerDuty trigger fired [dedup=%s] status=%s",
            dedup_key,
            result.get("status"),
        )
        return result
    except Exception as exc:
        logger.exception("Failed to fire PagerDuty alert: %s", exc)
        return None


def resolve_pagerduty_alert(routing_key: str, dedup_key: str) -> dict | None:
    """
    Send a RESOLVE event to PagerDuty, closing an open incident.

    Args:
        routing_key: Same key used in fire_pagerduty_alert
        dedup_key:   Same dedup key used in fire_pagerduty_alert

    Returns:
        PagerDuty API response dict, or None on error.
    """
    payload: dict[str, Any] = {
        "routing_key": routing_key,
        "event_action": "resolve",
        "dedup_key": dedup_key,
    }
    try:
        result = _send_event(payload)
        logger.info(
            "PagerDuty resolved [dedup=%s] status=%s",
            dedup_key,
            result.get("status"),
        )
        return result
    except Exception as exc:
        logger.exception("Failed to resolve PagerDuty alert: %s", exc)
        return None


def acknowledge_pagerduty_alert(routing_key: str, dedup_key: str) -> dict | None:
    """
    Send an ACKNOWLEDGE event to PagerDuty, suppressing escalation.
    """
    payload: dict[str, Any] = {
        "routing_key": routing_key,
        "event_action": "acknowledge",
        "dedup_key": dedup_key,
    }
    try:
        result = _send_event(payload)
        logger.info("PagerDuty acknowledged [dedup=%s]", dedup_key)
        return result
    except Exception as exc:
        logger.exception("Failed to acknowledge PagerDuty alert: %s", exc)
        return None


def make_dedup_key(tenant_id: str, signature: str) -> str:
    """
    Build a deterministic dedup key from tenant + error signature.
    This ensures the same error always maps to the same PagerDuty incident,
    so repeated OpsLens alerts cluster correctly instead of creating duplicates.
    """
    raw = f"opslens:{tenant_id}:{signature}"
    return hashlib.sha256(raw.encode()).hexdigest()[:40]


# ── Celery task (fire-and-forget) ─────────────────────────────────────────────

def dispatch_pagerduty_alerts(
    routing_targets: list[dict],
    tenant_id: str,
    error_signature: str,
    error_summary: str,
    service_name: str | None,
    severity: str,
    custom_details: dict | None = None,
) -> None:
    """
    Called from log_fast_alert.fast_scan after routing rules are evaluated.

    Iterates all matched routing rules that have a pagerduty_key and fires
    a PagerDuty trigger event for each one (different rules → different PD
    services/escalation policies).

    Args:
        routing_targets:  List of dicts from _evaluate_routing_rules —
                          each has keys: team_name, slack_webhook, email_recipients,
                          pagerduty_key, stop_on_match
        tenant_id:        Used to build a stable dedup key
        error_signature:  ErrorGroup.signature (MD5 hash of normalised exception line)
        error_summary:    Short human-readable description of the error
        service_name:     Service/container where the error occurred
        severity:         "critical" | "high" | "medium" | "low"
        custom_details:   Optional dict of extra fields to include in PD incident
    """
    dedup_key = make_dedup_key(tenant_id, error_signature)

    for target in routing_targets:
        pd_key = target.get("pagerduty_key")
        if not pd_key:
            continue

        team = target.get("team_name", "unknown")
        logger.info(
            "Dispatching PagerDuty alert to team=%s dedup=%s",
            team,
            dedup_key,
        )

        details = {
            "tenant_id": tenant_id,
            "error_signature": error_signature,
            "team": team,
            **(custom_details or {}),
        }

        fire_pagerduty_alert(
            routing_key=pd_key,
            summary=f"[OpsLens] {error_summary}",
            dedup_key=dedup_key,
            severity=severity,
            source=service_name or "OpsLens AI",
            component=service_name,
            group=team,
            custom_details=details,
        )
