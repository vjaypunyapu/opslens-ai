"""
OpsLens AI – Celery Application Factory + Beat Schedule
"""
from celery import Celery
from celery.schedules import crontab

from apps.api.config import settings

app = Celery(
    "opslens_worker",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=[
        "apps.worker.tasks.ingestion",
        "apps.worker.tasks.insight_runner",
        "apps.worker.tasks.alert_runner",
        "apps.worker.tasks.log_scanner",    # hourly LLM digest
        "apps.worker.tasks.log_fast_alert", # 5-min fast alert + RAG enrichment
        "apps.worker.tasks.rrt_briefing",   # structured RRT incident brief generation
        "apps.worker.tasks.master",         # fan-out tasks used by Beat schedule
        "apps.worker.tasks.retention",       # daily data retention cleanup
        "apps.worker.tasks.pagerduty",       # PagerDuty Events API v2 helpers
        "apps.worker.tasks.log_source_poller",  # sync_integration + poll_log_source
    ],
)

app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    # Global default — periodic/log tasks use acks_late so they're retried on
    # worker crash. sync_integration overrides this to acks_late=False because
    # it's a one-shot user-triggered task that must not loop forever on timeout.
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    # Global defaults for short-running tasks. sync_integration overrides these
    # with much higher limits (20/25 min) because full contextual syncs
    # (GitHub 50 repos, Slack 2yr history) legitimately need more time.
    task_soft_time_limit=300,   # 5 min soft limit
    task_time_limit=600,        # 10 min hard limit
    result_expires=86400,       # keep results for 24h
)

# ── Beat schedule ─────────────────────────────────────────────────────────────
# In production, the ALL_TENANT_IDS list is loaded dynamically from the DB.
# For simplicity this schedule dispatches the master fan-out task which then
# iterates all active tenants.
app.conf.beat_schedule = {
    # Run insight generation every hour for all tenants
    "insight-runner-hourly": {
        "task": "insights.run_all_tenants",
        "schedule": crontab(minute=0),  # top of every hour
    },
    # Evaluate alert rules every 15 minutes
    "alert-evaluator": {
        "task": "alerts.evaluate_all_tenants",
        "schedule": crontab(minute="*/15"),
    },
    # Trigger staging data processing every 5 minutes
    "staging-processor": {
        "task": "ingestion.process_all_staging",
        "schedule": crontab(minute="*/5"),
    },
    # Hourly LLM digest — summarises all issues from the past hour
    "log-scanner": {
        "task": "logs.scan_and_report",
        "schedule": crontab(minute=f"*/{settings.LOG_SCAN_CRON_MINUTES}"),
        "kwargs": {"tenant_id": None},
    },
    # Fast alert — runs every 5 min, fires immediately on new exceptions,
    # then dispatches RAG enrichment to find related Jira/Slack/GitHub context.
    # Fan-out to all tenants so routing rules are resolved per-tenant.
    "log-fast-alert": {
        "task": "logs.fast_scan_all_tenants",
        "schedule": crontab(minute=f"*/{settings.LOG_FAST_ALERT_CRON_MINUTES}"),
    },
    # Log source poller — directly pulls ERROR/WARN+ logs from Elasticsearch,
    # Datadog, CloudWatch, GCP Logging, Splunk, and Azure Monitor on behalf of
    # each tenant. Zero configuration required from customers — they connect
    # once via the UI and OpsLens polls automatically using last_synced_at as
    # an incremental cursor so each run only fetches NEW records.
    "log-source-poller": {
        "task": "logs.poll_all_log_sources",
        "schedule": crontab(minute=f"*/{settings.LOG_FAST_ALERT_CRON_MINUTES}"),
    },
    # Data retention cleanup — daily at 02:00 UTC
    # Deletes rows older than each tenant's RetentionPolicy thresholds
    "retention-cleanup": {
        "task": "retention.run_cleanup",
        "schedule": crontab(hour=2, minute=0),
        "kwargs": {"tenant_id": None},   # None = run for all tenants
    },
    # Re-embed any documents stuck in embedding_status='pending' due to
    # transient OpenAI or Qdrant failures. Runs every 30 minutes.
    "retry-pending-embeddings": {
        "task": "ingestion.retry_pending_embeddings",
        "schedule": crontab(minute="*/30"),
    },
    # Daily re-sync for contextual sources (GitHub, Jira, Slack, HubSpot,
    # Zendesk, Google Drive, Bitbucket). Schedule is configurable via env:
    #   CONTEXTUAL_SYNC_CRON_HOUR   — hour(s) in crontab syntax (default "3")
    #   CONTEXTUAL_SYNC_CRON_MINUTE — minute(s) in crontab syntax (default "0")
    # Examples:
    #   Every 6 hours:  CONTEXTUAL_SYNC_CRON_HOUR="*/6" CONTEXTUAL_SYNC_CRON_MINUTE="0"
    #   Twice daily:    CONTEXTUAL_SYNC_CRON_HOUR="3,15"
    #   Hourly:         CONTEXTUAL_SYNC_CRON_HOUR="*"
    "contextual-source-sync": {
        "task": "ingestion.sync_all_contextual_sources",
        "schedule": crontab(
            hour=settings.CONTEXTUAL_SYNC_CRON_HOUR,
            minute=settings.CONTEXTUAL_SYNC_CRON_MINUTE,
        ),
    },
}
