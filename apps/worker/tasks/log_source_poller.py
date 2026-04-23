"""
OpsLens AI — Log Source Poller
================================
Per-integration Celery task that directly polls external log systems
(Elasticsearch, Datadog, CloudWatch, GCP Logging, Splunk, Azure Monitor)
on a regular schedule, with zero configuration required from the customer.

The customer connects once via the OpsLens UI (entering their API key /
credentials). From that point on, this task runs every 5 minutes and
fetches only new logs since the last successful sync (incremental, using
integration.last_synced_at as the `since` cursor).

Flow:
  poll_all_log_sources (Beat, every 5 min)
    → poll_log_source.delay(integration_id, tenant_id, source_type)
      → decrypt credentials
      → call direct_sync_service.run_direct_sync()
      → run_direct_sync calls the appropriate fetcher with since=last_synced_at
      → fetcher pulls new ERROR/WARN+ records, saves + embeds them
      → last_synced_at updated — next poll will only fetch newer records
"""
from __future__ import annotations

import logging

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded

from ..async_utils import run_async as _run_async

logger = logging.getLogger(__name__)


@shared_task(
    name="logs.poll_log_source",
    bind=True,
    max_retries=2,
    default_retry_delay=60,       # wait 60s before retry
    soft_time_limit=240,          # 4 min soft limit per source
    time_limit=300,               # 5 min hard kill — never block the next cycle
    acks_late=True,
)
def poll_log_source(self, integration_id: str, tenant_id: str, source_type: str) -> dict:
    """
    Pull new logs from a single external log-source integration.
    Delegates entirely to run_direct_sync which handles credentials,
    incremental since-cursor, normalisation, embedding, and status updates.
    """
    logger.info(
        "poll_log_source: starting source_type=%s integration=%s tenant=%s",
        source_type, integration_id, tenant_id,
    )
    try:
        from ...api.services.direct_sync_service import run_direct_sync
        _run_async(run_direct_sync(integration_id, tenant_id))
        logger.info(
            "poll_log_source: done source_type=%s integration=%s",
            source_type, integration_id,
        )
        return {"status": "ok", "integration_id": integration_id, "source_type": source_type}

    except SoftTimeLimitExceeded:
        # Soft limit hit — we have a brief window before the hard SIGKILL.
        # Mark the integration as error so status doesn't stay "pending" forever.
        logger.warning(
            "poll_log_source: soft time limit exceeded source_type=%s integration=%s",
            source_type, integration_id,
        )
        _run_async(_mark_integration_error(integration_id, "Sync timed out (source too large or slow)"))
        return {"status": "timeout", "integration_id": integration_id}

    except Exception as exc:
        logger.warning(
            "poll_log_source: failed source_type=%s integration=%s: %s",
            source_type, integration_id, exc,
        )
        # Retry up to max_retries times before giving up and marking error
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            _run_async(_mark_integration_error(integration_id, str(exc)))
            return {"status": "error", "integration_id": integration_id, "error": str(exc)}


@shared_task(
    name="ingestion.sync_integration",
    bind=True,
    # Full contextual syncs (GitHub 50 repos, Slack 2yr history) need much
    # more than 5 min. Hard kill at 25 min; soft limit at 20 min gives us a
    # clean window to mark status=error before SIGKILL arrives.
    # acks_late=False (default) so the message is acked on delivery — a
    # one-shot user-triggered sync should not loop forever if the worker
    # crashes mid-run.
    max_retries=1,
    default_retry_delay=30,
    soft_time_limit=1200,   # 20 min soft — throws SoftTimeLimitExceeded
    time_limit=1500,        # 25 min hard kill
    acks_late=False,
)
def sync_integration(self, integration_id: str, tenant_id: str) -> dict:
    """
    Run a full direct sync for any integration type (GitHub, Jira, Slack,
    Railway, etc.) as a Celery task so it survives pod restarts and can be
    retried on failure. Used by the manual sync endpoint and initial sync
    on integration creation.
    """
    logger.info("sync_integration: starting integration=%s tenant=%s", integration_id, tenant_id)
    try:
        from ...api.services.direct_sync_service import run_direct_sync
        _run_async(run_direct_sync(integration_id, tenant_id))
        logger.info("sync_integration: done integration=%s", integration_id)
        return {"status": "ok", "integration_id": integration_id}

    except SoftTimeLimitExceeded:
        # Mark error before the hard SIGKILL arrives so status never stays pending
        logger.warning("sync_integration: soft time limit exceeded integration=%s", integration_id)
        _run_async(_mark_integration_error(
            integration_id,
            "Sync timed out after 20 min — your data source may be very large. "
            "Partial data has been indexed. Trigger a manual sync to continue.",
        ))
        return {"status": "timeout", "integration_id": integration_id}

    except Exception as exc:
        logger.warning("sync_integration: failed integration=%s: %s", integration_id, exc)
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            _run_async(_mark_integration_error(integration_id, str(exc)))
            return {"status": "error", "integration_id": integration_id, "error": str(exc)}


async def _mark_integration_error(integration_id: str, error_msg: str) -> None:
    """Mark the integration as error in the DB so the UI can surface it."""
    import uuid
    import sqlalchemy as sa
    from ...api.db.models import Integration
    from ..db import AsyncSession

    try:
        async with AsyncSession() as db:
            await db.execute(
                sa.update(Integration)
                .where(Integration.id == uuid.UUID(integration_id))
                .values(status="error", error_message=error_msg[:500])
            )
            await db.commit()
    except Exception as exc:
        logger.warning("Could not mark integration %s as error: %s", integration_id, exc)
