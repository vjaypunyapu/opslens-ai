#!/usr/bin/env python3
"""
OpsLens AI — Local Dev Bootstrap / Seed Script
================================================
Run this ONCE after starting the API for the first time to pre-populate
the database with everything you need to test OpsLens locally:

    python scripts/seed_dev.py

What it creates:
  ✅ 1 test tenant  (id = "default")
  ✅ 4 alert routing rules  (Payments, Auth, Data, Catch-all)
  ✅ 3 known issue suppressions  (common noisy errors)
  ✅ 1 retention policy  (reasonable dev defaults)
  ✅ 3 sample timeline events  (1 deploy + 1 PR + 1 Jira)
  ✅ 1 RBAC role assignment  (admin role for dev user)

Options:
  --base-url  API base URL          (default: http://localhost:8080)
  --tenant    Tenant ID to seed     (default: default)
  --webhook   Slack/Teams webhook   (default: empty — alerts won't fire)
  --reset     Delete existing seed data first (careful!)

Examples:
  python scripts/seed_dev.py
  python scripts/seed_dev.py --webhook https://hooks.slack.com/services/xxx
  python scripts/seed_dev.py --base-url http://localhost:8080 --tenant acme
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

# Use only stdlib — no extra dependencies required
try:
    import urllib.request
    import urllib.error
except ImportError:
    print("ERROR: Python stdlib required (urllib). Python 3.8+ supported.")
    sys.exit(1)


# ── Config ────────────────────────────────────────────────────────────────────

DEFAULT_BASE_URL = "http://localhost:8080"
DEFAULT_TENANT   = "default"

COLORS = {
    "green":  "\033[92m",
    "yellow": "\033[93m",
    "red":    "\033[91m",
    "cyan":   "\033[96m",
    "reset":  "\033[0m",
    "bold":   "\033[1m",
}


def c(color: str, text: str) -> str:
    return f"{COLORS.get(color,'')}{text}{COLORS['reset']}"


def ok(msg: str):  print(f"  {c('green','✓')} {msg}")
def warn(msg: str): print(f"  {c('yellow','⚠')} {msg}")
def err(msg: str):  print(f"  {c('red','✗')} {msg}")
def info(msg: str): print(f"  {c('cyan','→')} {msg}")


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _request(method: str, url: str, body: dict | None = None, token: str | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body else None
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(body_text)
        except Exception:
            return e.code, {"detail": body_text[:200]}


def post(base: str, path: str, body: dict, tenant: str, token: str = "dev-token") -> dict | None:
    sep = "&" if "?" in path else "?"
    url = f"{base}{path}{sep}tenant_id={tenant}"
    status, resp = _request("POST", url, body, token)
    if status in (200, 201):
        return resp
    elif status == 409:
        warn(f"Already exists (409): {path}")
        return None
    else:
        err(f"POST {path} → {status}: {resp.get('detail', resp)}")
        return None


def get(base: str, path: str, tenant: str, token: str = "dev-token") -> dict | None:
    sep = "&" if "?" in path else "?"
    url = f"{base}{path}{sep}tenant_id={tenant}"
    status, resp = _request("GET", url, token=token)
    if status == 200:
        return resp
    return None


# ── Seed functions ────────────────────────────────────────────────────────────

def seed_routing_rules(base: str, tenant: str, webhook: str) -> int:
    print(f"\n{c('bold','── Alert Routing Rules')}")
    rules = [
        {
            "team_name": "Payments Team",
            "description": "Catches payment, billing, and Stripe errors",
            "service_patterns": ["payment", "stripe", "checkout", "billing", "invoice"],
            "error_patterns": ["PaymentError", "StripeError", "ChargeFailure", "CardDeclined"],
            "source_containers": ["payment-service", "billing-worker"],
            "slack_webhook": webhook or "",
            "priority": 10,
            "stop_on_match": False,
        },
        {
            "team_name": "Auth & Security",
            "description": "Catches authentication, JWT, and session errors",
            "service_patterns": ["auth", "jwt", "session", "login", "oauth", "token"],
            "error_patterns": ["AuthError", "InvalidSignature", "TokenExpired", "PermissionDenied"],
            "source_containers": ["auth-service", "identity-provider"],
            "slack_webhook": webhook or "",
            "priority": 20,
            "stop_on_match": False,
        },
        {
            "team_name": "Data Platform",
            "description": "Catches database, query, and pipeline errors",
            "service_patterns": ["database", "sqlalchemy", "postgres", "qdrant", "redis", "pipeline"],
            "error_patterns": ["OperationalError", "ConnectionRefused", "QueryTimeout", "IntegrityError"],
            "source_containers": ["db-migrate", "data-pipeline", "etl-worker"],
            "slack_webhook": webhook or "",
            "priority": 30,
            "stop_on_match": False,
        },
        {
            "team_name": "Eng On-Call",
            "description": "Catch-all — everything not matched by a specific team",
            "service_patterns": [],
            "error_patterns": ["Error", "Exception", "Critical"],
            "source_containers": [],
            "slack_webhook": webhook or "",
            "priority": 999,
            "stop_on_match": True,
        },
    ]

    created = 0
    for rule in rules:
        result = post(base, "/api/v1/log-ops/routing-rules", rule, tenant)
        if result:
            ok(f"Routing rule: {rule['team_name']} (priority {rule['priority']})")
            created += 1
    return created


def seed_known_issues(base: str, tenant: str) -> int:
    print(f"\n{c('bold','── Known Issue Suppressions')}")
    issues = [
        {
            "signature": None,
            "match_pattern": "HealthCheck.*timeout",
            "description": "Routine health check timeout — not actionable",
            "suppressed_by": "seed-script",
        },
        {
            "signature": None,
            "match_pattern": "ConnectionPool.*closed",
            "description": "Expected connection pool cleanup on graceful shutdown",
            "suppressed_by": "seed-script",
        },
        {
            "signature": None,
            "match_pattern": "celery.*beat.*already running",
            "description": "Celery Beat duplicate start warning — harmless",
            "suppressed_by": "seed-script",
        },
    ]

    created = 0
    for issue in issues:
        result = post(base, "/api/v1/log-ops/known-issues", issue, tenant)
        if result:
            ok(f"Known issue: {issue['match_pattern']}")
            created += 1
    return created


def seed_retention_policy(base: str, tenant: str) -> bool:
    print(f"\n{c('bold','── Retention Policy')}")
    policy = {
        "log_scan_history_days": 30,
        "timeline_event_days":   60,
        "rrt_brief_days":        90,
        "audit_log_days":        365,
        "chat_session_days":     30,
        "insight_days":          30,
        "embedding_days":        60,
        "archive_to_s3":         False,
    }
    result = post(base, "/api/v1/retention", policy, tenant)
    if result:
        ok("Retention policy created (30d log history, 90d RRT briefs, 365d audit logs)")
        return True
    return False


def seed_timeline_events(base: str, tenant: str) -> int:
    print(f"\n{c('bold','── Sample Timeline Events')}")
    now = datetime.now(tz=timezone.utc)

    events = [
        {
            "source_type":  "github_deploy",
            "occurred_at":  now.isoformat(),
            "title":        "Deploy payment-service v2.4.1 → production",
            "description":  "Merged PR #312: Add retry logic for Stripe webhook delivery",
            "service":      "payment-service",
            "team":         "Payments Team",
            "actor":        "dev-seed",
            "severity":     "info",
            "source_id":    "seed:deploy:payment-service:v2.4.1",
        },
        {
            "source_type":  "github_pr",
            "occurred_at":  now.isoformat(),
            "title":        "PR #315 merged: Fix JWT expiry edge case in auth middleware",
            "description":  "Closes OPS-88. Changed expiry buffer from 30s to 120s.",
            "service":      "auth-service",
            "team":         "Auth & Security",
            "actor":        "dev-seed",
            "severity":     "info",
            "source_id":    "seed:pr:auth-service:315",
        },
        {
            "source_type":  "jira_issue",
            "occurred_at":  now.isoformat(),
            "title":        "OPS-142: Payment timeouts during peak load",
            "description":  "Status moved to In Progress. Assigned to payments team.",
            "service":      "payment-service",
            "team":         "Payments Team",
            "actor":        "dev-seed",
            "severity":     "high",
            "source_id":    "seed:jira:OPS-142",
        },
    ]

    created = 0
    for event in events:
        result = post(base, "/api/v1/timeline/events", event, tenant)
        if result:
            ok(f"Timeline event: [{event['source_type']}] {event['title'][:60]}")
            created += 1
    return created


def seed_rbac_role(base: str, tenant: str) -> bool:
    print(f"\n{c('bold','── RBAC Role Assignment')}")
    role = {
        "user_id": "dev-user-local",
        "role":    "admin",
        "notes":   "Seeded by seed_dev.py for local testing",
    }
    result = post(base, "/api/v1/rbac/roles", role, tenant)
    if result:
        ok(f"Role assignment: dev-user-local → admin")
        return True
    return False


def seed_demo_rrt_brief(base: str, tenant: str) -> bool:
    print(f"\n{c('bold','── Demo RRT Brief (payment-service Stripe timeout)')}")
    url = f"{base}/api/v1/rrt-briefs/seed-demo?tenant_id={tenant}"
    status, resp = _request("POST", url, token="dev-token")
    if status == 201:
        ok(f"Demo RRT brief seeded: [{resp.get('jira_ticket_key','?')}] {resp.get('title','')[:70]}")
        ok(f"  Brief ID : {resp.get('id','?')}")
        ok(f"  Jira URL : {resp.get('jira_ticket_url','?')}")
        return True
    elif status == 200:
        ok(f"Demo brief already existed, refreshed.")
        return True
    else:
        warn(f"Seed demo brief returned {status}: {resp.get('detail', resp)}")
        return False


def seed_demo_qdrant_docs(base: str, tenant: str) -> bool:
    """
    Ingest a synthetic Jira doc about Stripe timeouts into Qdrant so that
    when the real simulate pipeline runs enrichment, it finds rich context.
    """
    print(f"\n{c('bold','── Demo Qdrant Context (Jira + Slack docs for RAG enrichment)')}")
    docs = [
        {
            "source_type": "jira",
            "source_id":   "DEMO-OPS-142",
            "title":       "OPS-142: Payment timeouts during peak load — Stripe API degradation",
            "content": (
                "Recurring Stripe API timeouts observed during peak traffic windows on payment-service. "
                "PaymentError: Stripe API timeout after 30s — retries exhausted. "
                "Root cause: missing exponential backoff in payment-service retry logic. "
                "Stripe /v1/charges endpoint P99 latency elevated to 28s (normal 800ms). "
                "Fix implemented: jitter + backoff with 60s timeout and circuit breaker. "
                "Related PR #312 reduced timeout from 60s to 30s — possible regression source. "
                "Assigned to Payments Team. Status: In Progress."
            ),
            "metadata": {
                "url":        "https://your-jira.atlassian.net/browse/OPS-142",
                "author":     "payments-team",
                "created_at": "2026-08-10T14:00:00Z",
            },
        },
        {
            "source_type": "slack",
            "source_id":   "DEMO-SLACK-stripe-alert-001",
            "title":       "#payments-alerts — Stripe elevated error rates (2026-08-12)",
            "content": (
                "stripe_bot: ⚠️ Elevated API error rates detected on Stripe /v1/charges endpoint. "
                "P99 latency: 28s (normal: 800ms). Started approximately 00:24 UTC on 2026-08-12. "
                "payment-service PaymentError: Stripe API timeout after 30s retries exhausted. "
                "Stripe status page updated. Engineering investigating. "
                "Payments Team on-call notified."
            ),
            "metadata": {
                "url":        None,
                "author":     "stripe_bot",
                "created_at": "2026-08-12T00:24:00Z",
            },
        },
        {
            "source_type": "github",
            "source_id":   "DEMO-PR-312",
            "title":       "PR #312: Add retry logic for Stripe webhook delivery — payment-service",
            "content": (
                "Merged PR #312 into payment-service main branch. "
                "Changed Stripe client timeout from 60s to 30s to align with API gateway limits. "
                "Added retry logic for webhook delivery. "
                "File changed: payment-service/stripe_client.py line 87 function charge_card. "
                "Timeout parameter reduced: timeout=60 changed to timeout=30. "
                "This change may have introduced a regression causing PaymentError Stripe API timeout "
                "when Stripe latency is elevated. Consider reverting as immediate mitigation."
            ),
            "metadata": {
                "url":        "https://github.com/your-org/payment-service/pull/312",
                "author":     "dev-seed",
                "created_at": "2026-08-11T22:15:00Z",
            },
        },
    ]

    created = 0
    for doc in docs:
        result = post(base, "/api/v1/ingestion", doc, tenant)
        if result:
            ok(f"Qdrant doc: [{doc['source_type']}] {doc['title'][:60]}")
            created += 1
        else:
            warn(f"Could not ingest doc: {doc['source_id']} (ingestion endpoint may need worker)")

    return created > 0


def check_api(base: str) -> bool:
    print(f"\n{c('bold','── API Health Check')}")
    try:
        status, resp = _request("GET", f"{base}/health")
        if status == 200:
            ok(f"API is up: {resp}")
            return True
        warn(f"API returned {status}: {resp}")
        return False
    except Exception as exc:
        err(f"Cannot reach API at {base}: {exc}")
        return False


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="OpsLens AI — Local dev seed script",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="API base URL")
    parser.add_argument("--tenant",   default=DEFAULT_TENANT,   help="Tenant ID")
    parser.add_argument("--webhook",  default="",               help="Slack/Teams webhook URL for routing rules")
    args = parser.parse_args()

    base    = args.base_url.rstrip("/")
    tenant  = args.tenant
    webhook = args.webhook

    print(f"\n{c('bold', c('cyan', '🌱 OpsLens AI — Dev Seed Script'))}")
    print(f"   Base URL : {base}")
    print(f"   Tenant   : {tenant}")
    print(f"   Webhook  : {webhook or c('yellow','(none — alerts will not fire)')}")

    # Step 0: verify API is up
    if not check_api(base):
        print(f"\n{c('red','ERROR')}: Cannot reach the API. Make sure it is running:")
        print(f"  uvicorn apps.api.main:app --reload --port 8080\n")
        sys.exit(1)

    # Step 1–7: seed data
    rules      = seed_routing_rules(base, tenant, webhook)
    issues     = seed_known_issues(base, tenant)
    ret_ok     = seed_retention_policy(base, tenant)
    events     = seed_timeline_events(base, tenant)
    role_ok    = seed_rbac_role(base, tenant)
    demo_brief = seed_demo_rrt_brief(base, tenant)
    demo_qdrant = seed_demo_qdrant_docs(base, tenant)

    # Summary
    print(f"\n{c('bold', c('green', '── Seed Complete ─────────────────────'))}")
    print(f"  Routing rules    : {rules}")
    print(f"  Known issues     : {issues}")
    print(f"  Retention policy : {'✓' if ret_ok else '✗'}")
    print(f"  Timeline events  : {events}")
    print(f"  RBAC role        : {'✓' if role_ok else '✗'}")
    print(f"  Demo RRT brief   : {'✓' if demo_brief else '✗'}")
    print(f"  Demo Qdrant docs : {'✓' if demo_qdrant else '✗ (start worker to ingest)'}")

    print(f"""
{c('bold','── What to try next ──────────────────────')}

  1. Write test errors to a log file:
     {c('cyan', 'for i in {1..5}; do')}
     {c('cyan', '  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) ERROR PaymentError: Card declined $i" >> /tmp/opslens-test.log')}
     {c('cyan', 'done')}

  2. Trigger a fast scan:
     {c('cyan', 'curl -X POST "{base}/api/v1/alerts/logs/scan?tenant_id={tenant}" \\')}
     {c('cyan', '     -H "Authorization: Bearer dev-token"')}

  3. Check what fired:
     {c('cyan', f'curl "{base}/api/v1/rrt-briefs?tenant_id={tenant}" -H "Authorization: Bearer dev-token"')}
     {c('cyan', f'curl "{base}/api/v1/timeline?tenant_id={tenant}" -H "Authorization: Bearer dev-token"')}

  4. Manager dashboard:
     {c('cyan', f'curl "{base}/api/v1/manager/summary?tenant_id={tenant}&window_days=1" -H "Authorization: Bearer dev-token"')}
""")


if __name__ == "__main__":
    main()
