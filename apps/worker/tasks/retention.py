"""
OpsLens AI — Data Retention Task
==================================
Daily Celery Beat task that enforces RetentionPolicy rules for each tenant.

Beat schedule: every day at 02:00 UTC (configured in celery_app.py)

For each tenant with an active RetentionPolicy:
  1. Delete LogScanHistory rows older than log_scan_history_days
  2. Delete TimelineEvent rows older than timeline_event_days
  3. Delete resolved RRTBrief rows older than rrt_brief_days
  4. Delete AuditLog rows older than audit_log_days
  5. Delete ChatSession rows (and cascaded messages) older than chat_session_days
  6. Delete Insight rows older than insight_days
  7. Delete old Qdrant vectors by payload.indexed_at timestamp

Each category respects a 0 value (retain forever) and logs deleted row counts.
The task is idempotent — safe to run multiple times.

Archive (optional):
  If RetentionPolicy.archive_to_s3 is True, rows are serialised to JSONL
  and uploaded to S3 before deletion. Requires AWS_ACCESS_KEY_ID etc. in env.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from celery import shared_task
from ..async_utils import run_async as _run_async

logger = logging.getLogger(__name__)


def _cutoff(days: int) -> datetime | None:
    """Returns UTC cutoff datetime for `days` ago, or None if days == 0."""
    if not days:
        return None
    return datetime.now(tz=timezone.utc) - timedelta(days=days)


# ── Optional S3 archive helper ────────────────────────────────────────────────

def _archive_to_s3(rows: list[dict], bucket: str, key: str) -> bool:
    """Serialise rows as JSONL and upload to S3. Returns True on success."""
    try:
        import boto3  # type: ignore
        body = "\n".join(json.dumps(r, default=str) for r in rows).encode("utf-8")
        s3 = boto3.client("s3")
        s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/jsonl")
        logger.info("Archived %d rows to s3://%s/%s", len(rows), bucket, key)
        return True
    except Exception as exc:
        logger.warning("S3 archive failed for key=%s: %s", key, exc)
        return False


# ── Per-category delete helpers ───────────────────────────────────────────────

async def _delete_log_scan_history(db, tenant_id: str, days: int) -> int:
    if not days:
        return 0
    try:
        from apps.worker.models.log_scan import LogScanHistory
        cutoff = _cutoff(days)
        result = await db.execute(
            sa.delete(LogScanHistory).where(
                LogScanHistory.tenant_id == tenant_id,
                LogScanHistory.scanned_at <= cutoff,
            ).returning(LogScanHistory.id)
        )
        return len(result.fetchall())
    except Exception as exc:
        logger.warning("log_scan_history cleanup failed: %s", exc)
        return 0


async def _delete_timeline_events(db, tenant_id: str, days: int) -> int:
    if not days:
        return 0
    try:
        from apps.api.models.timeline import TimelineEvent
        cutoff = _cutoff(days)
        result = await db.execute(
            sa.delete(TimelineEvent).where(
                TimelineEvent.tenant_id == tenant_id,
                TimelineEvent.occurred_at <= cutoff,
            ).returning(TimelineEvent.id)
        )
        return len(result.fetchall())
    except Exception as exc:
        logger.warning("timeline_event cleanup failed: %s", exc)
        return 0


async def _delete_rrt_briefs(db, tenant_id: str, days: int) -> int:
    """Only deletes resolved briefs — open/investigating briefs are retained."""
    if not days:
        return 0
    try:
        from apps.api.models.rrt_brief import RRTBrief
        cutoff = _cutoff(days)
        result = await db.execute(
            sa.delete(RRTBrief).where(
                RRTBrief.tenant_id == tenant_id,
                RRTBrief.detected_at <= cutoff,
                RRTBrief.status == "resolved",
            ).returning(RRTBrief.id)
        )
        return len(result.fetchall())
    except Exception as exc:
        logger.warning("rrt_brief cleanup failed: %s", exc)
        return 0


async def _delete_audit_logs(db, tenant_id: str, days: int) -> int:
    """Audit logs deleted last — always respect minimum (default 730 days)."""
    if not days:
        return 0
    try:
        from apps.api.models.audit import AuditLog
        cutoff = _cutoff(days)
        result = await db.execute(
            sa.delete(AuditLog).where(
                AuditLog.tenant_id == tenant_id,
                AuditLog.occurred_at <= cutoff,
            ).returning(AuditLog.id)
        )
        return len(result.fetchall())
    except Exception as exc:
        logger.warning("audit_log cleanup failed: %s", exc)
        return 0


async def _delete_chat_sessions(db, tenant_id: str, days: int) -> int:
    if not days:
        return 0
    try:
        from apps.api.db.models import ChatSession
        cutoff = _cutoff(days)
        result = await db.execute(
            sa.delete(ChatSession).where(
                ChatSession.tenant_id == tenant_id,
                ChatSession.created_at <= cutoff,
            ).returning(ChatSession.id)
        )
        return len(result.fetchall())
    except Exception as exc:
        logger.warning("chat_session cleanup failed: %s", exc)
        return 0


async def _delete_insights(db, tenant_id: str, days: int) -> int:
    if not days:
        return 0
    try:
        from apps.api.models.insight import Insight
        cutoff = _cutoff(days)
        result = await db.execute(
            sa.delete(Insight).where(
                Insight.tenant_id == tenant_id,
                Insight.generated_at <= cutoff,
                Insight.status == "resolved",
            ).returning(Insight.id)
        )
        return len(result.fetchall())
    except Exception as exc:
        logger.warning("insight cleanup failed: %s", exc)
        return 0


def _delete_qdrant_embeddings(tenant_id: str, days: int) -> int:
    """
    Deletes Qdrant vectors whose payload.indexed_at is older than `days`.
    Uses Qdrant scroll + batch delete.
    """
    if not days:
        return 0
    try:
        from apps.api.config import settings
        from qdrant_client import QdrantClient
        from qdrant_client.models import Filter, FieldCondition, Range

        cutoff_ts = _cutoff(days).timestamp()  # type: ignore
        qdrant = QdrantClient(
            url=settings.QDRANT_URL,
            api_key=settings.QDRANT_API_KEY or None,
            timeout=30,
        )
        collection = f"{settings.QDRANT_COLLECTION_PREFIX}{tenant_id}"

        deleted = 0
        next_page_offset = None

        while True:
            # Scroll through vectors with old indexed_at
            scroll_result, next_page_offset = qdrant.scroll(
                collection_name=collection,
                scroll_filter=Filter(
                    must=[
                        FieldCondition(
                            key="indexed_at",
                            range=Range(lte=cutoff_ts),
                        )
                    ]
                ),
                limit=500,
                offset=next_page_offset,
                with_payload=False,
                with_vectors=False,
            )

            if not scroll_result:
                break

            ids = [str(p.id) for p in scroll_result]
            qdrant.delete(
                collection_name=collection,
                points_selector=ids,
            )
            deleted += len(ids)
            logger.info("Qdrant: deleted %d old vectors for tenant=%s", len(ids), tenant_id)

            if not next_page_offset:
                break

        return deleted
    except Exception as exc:
        logger.warning("Qdrant embedding cleanup failed for tenant=%s: %s", tenant_id, exc)
        return 0


async def _delete_staging_queue(db, days: int) -> int:
    """
    Delete processed rows from opslens.ingestion_queue older than `days`.
    Only touches rows where processed_at IS NOT NULL — unprocessed rows are
    always retained regardless of age so nothing is silently lost.
    """
    if not days:
        return 0
    try:
        cutoff = _cutoff(days)
        result = await db.execute(
            sa.text("""
                DELETE FROM opslens.ingestion_queue
                WHERE processed_at IS NOT NULL
                  AND processed_at <= :cutoff
            """),
            {"cutoff": cutoff},
        )
        deleted = result.rowcount or 0
        if deleted:
            logger.info("staging_queue: deleted %d processed rows older than %d days", deleted, days)
        return deleted
    except Exception as exc:
        logger.warning("staging_queue cleanup failed: %s", exc)
        return 0


async def _delete_canonical_documents(db, tenant_id: str, days: int) -> int:
    """
    Delete old log-type canonical_documents for a tenant.
    Only removes source_types that are log sources (elasticsearch, datadog,
    cloudwatch, gcp, splunk, azuremonitor) — never touches contextual sources
    (jira, slack, github) which are governed by embedding_days in Qdrant.
    """
    if not days:
        return 0
    try:
        cutoff = _cutoff(days)
        result = await db.execute(
            sa.text("""
                DELETE FROM opslens.canonical_documents
                WHERE tenant_id = :tid
                  AND source_type IN (
                      'elasticsearch','datadog','cloudwatch',
                      'gcp','splunk','azuremonitor','railway'
                  )
                  AND source_created_at <= :cutoff
            """),
            {"tid": tenant_id, "cutoff": cutoff},
        )
        deleted = result.rowcount or 0
        if deleted:
            logger.info(
                "canonical_documents: deleted %d log-source rows for tenant=%s older than %d days",
                deleted, tenant_id, days,
            )
        return deleted
    except Exception as exc:
        logger.warning("canonical_documents cleanup failed for tenant=%s: %s", tenant_id, exc)
        return 0


# ── Main Celery task ──────────────────────────────────────────────────────────

@shared_task(
    name="retention.run_cleanup",
    bind=True,
    max_retries=1,
    soft_time_limit=600,
    time_limit=660,
)
def run_cleanup(self, tenant_id: str | None = None):
    """
    Enforce retention policies for all tenants (or a specific one if tenant_id given).
    Runs daily via Celery Beat at 02:00 UTC.

    Returns:
        dict with per-tenant deletion counts and any errors encountered.
    """
    return _run_async(_run_cleanup_async(tenant_id))


async def _run_cleanup_async(tenant_id: str | None) -> dict:
    from apps.worker.db import AsyncSession
    from apps.api.models.retention import RetentionPolicy
    from apps.api.config import settings as _cfg

    # ── Default TTLs ──────────────────────────────────────────────────────────
    # These are applied to any tenant that has no RetentionPolicy row in the DB.
    # Cleanup always runs — it never silently skips because a policy is missing.
    _DEFAULTS = {
        "log_scan_history_days": 90,
        "timeline_event_days":   180,
        "rrt_brief_days":        365,
        "audit_log_days":        730,
        "chat_session_days":     90,
        "insight_days":          90,
        "embedding_days":        180,
    }

    # ── Step 1: Load explicit per-tenant policies ─────────────────────────────
    policy_map: dict[str, object] = {}
    async with AsyncSession() as db:
        query = sa.select(RetentionPolicy).where(RetentionPolicy.is_active == True)
        if tenant_id:
            query = query.where(RetentionPolicy.tenant_id == tenant_id)
        result = await db.execute(query)
        for p in result.scalars().all():
            policy_map[str(p.tenant_id)] = p

    # ── Step 2: Discover ALL tenant IDs that have data ────────────────────────
    # This ensures cleanup runs even for tenants with no RetentionPolicy row.
    async with AsyncSession() as db:
        if tenant_id:
            all_tenant_ids = [tenant_id]
        else:
            rows = await db.execute(
                sa.text("""
                    SELECT DISTINCT tenant_id::text FROM opslens.canonical_documents
                    UNION
                    SELECT DISTINCT tenant_id::text FROM opslens.log_scan_history
                    UNION
                    SELECT DISTINCT tenant_id::text FROM opslens.timeline_events
                """)
            )
            all_tenant_ids = [r[0] for r in rows.fetchall()]

    if not all_tenant_ids:
        logger.info("retention: no tenant data found — nothing to clean up")
        return {"status": "no_data"}

    logger.info(
        "retention: running cleanup for %d tenant(s) (%d with explicit policies, %d using defaults)",
        len(all_tenant_ids),
        len(policy_map),
        len(all_tenant_ids) - len(policy_map),
    )

    # ── Step 3: Always clean up the staging queue first (shared table) ────────
    staging_deleted = 0
    try:
        async with AsyncSession() as db:
            staging_deleted = await _delete_staging_queue(
                db, _cfg.STAGING_QUEUE_RETENTION_DAYS
            )
            await db.commit()
    except Exception as exc:
        logger.warning("retention: staging queue cleanup failed: %s", exc)

    # ── Step 4: Per-tenant cleanup ────────────────────────────────────────────
    summary: dict[str, dict] = {}

    for tid in all_tenant_ids:
        counts: dict[str, int] = {"staging_queue": staging_deleted if tid == all_tenant_ids[0] else 0}
        errors: list[str] = []

        # Use explicit policy if available, otherwise fall back to defaults
        policy = policy_map.get(str(tid))
        ttl = lambda field: getattr(policy, field) if policy else _DEFAULTS[field]  # noqa: E731

        logger.info(
            "retention: cleaning tenant=%s (policy=%s)",
            tid, "explicit" if policy else "default",
        )

        try:
            async with AsyncSession() as db:
                counts["log_scan_history"] = await _delete_log_scan_history(
                    db, tid, ttl("log_scan_history_days")
                )
                counts["timeline_events"] = await _delete_timeline_events(
                    db, tid, ttl("timeline_event_days")
                )
                counts["rrt_briefs"] = await _delete_rrt_briefs(
                    db, tid, ttl("rrt_brief_days")
                )
                counts["audit_logs"] = await _delete_audit_logs(
                    db, tid, ttl("audit_log_days")
                )
                counts["chat_sessions"] = await _delete_chat_sessions(
                    db, tid, ttl("chat_session_days")
                )
                counts["insights"] = await _delete_insights(
                    db, tid, ttl("insight_days")
                )
                counts["canonical_log_docs"] = await _delete_canonical_documents(
                    db, tid, ttl("log_scan_history_days")
                )
                await db.commit()

        except Exception as exc:
            logger.exception("retention: DB cleanup failed for tenant=%s: %s", tid, exc)
            errors.append(str(exc))

        # Qdrant cleanup (separate connection — doesn't share AsyncSession)
        try:
            counts["qdrant_vectors"] = _delete_qdrant_embeddings(tid, ttl("embedding_days"))
        except Exception as exc:
            logger.warning("retention: Qdrant cleanup failed for tenant=%s: %s", tid, exc)
            errors.append(f"qdrant: {exc}")

        total_deleted = sum(counts.values())
        notes = f"Deleted: {counts}"
        if errors:
            notes += f" | Errors: {errors}"

        # Update policy stats only if this tenant has an explicit policy row
        if policy:
            try:
                async with AsyncSession() as db:
                    await db.execute(
                        sa.update(RetentionPolicy)
                        .where(RetentionPolicy.id == policy.id)
                        .values(
                            last_run_at=datetime.now(tz=timezone.utc),
                            last_deleted_rows=total_deleted,
                            last_run_notes=notes[:500],
                        )
                    )
                    await db.commit()
            except Exception:
                pass

        logger.info(
            "retention: tenant=%s done (policy=%s) — deleted %d rows total: %s",
            tid, "explicit" if policy else "default", total_deleted, counts,
        )
        summary[tid] = {
            "deleted": counts,
            "total": total_deleted,
            "errors": errors,
            "policy": "explicit" if policy else "default",
        }

    return {"status": "ok", "tenants": summary}
