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
        "apps.worker.tasks.master",      # fan-out tasks used by Beat schedule
    ],
)

app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
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
}
