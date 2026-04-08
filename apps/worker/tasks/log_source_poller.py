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

    except Exception as exc:
        logger.warning(
            "poll_log_source: failed source_type=%s integration=%s: %s",
            source_type, integration_id, exc,
        )
        # Retry up to max_retries times before giving up and marking error
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            # After all retries exhausted, mark the integration as error
            # so the customer sees it in the UI rather than silent failure
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
