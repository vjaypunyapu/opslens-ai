"""
OpsLens AI — Ingestion / Integrations Router
=============================================
Manages Airbyte-backed data source connections and exposes
manual sync triggers + status endpoints.

Endpoints:
    GET    /api/v1/integrations                      — List configured integrations
    POST   /api/v1/integrations                      — Connect a new source
    DELETE /api/v1/integrations/{id}                 — Disconnect a source
    POST   /api/v1/integrations/{id}/sync            — Trigger manual re-sync
    GET    /api/v1/integrations/{id}/status          — Sync status + stats
    POST   /api/v1/webhooks/airbyte                  — Receive Airbyte sync-complete webhooks

Dead-letter queue endpoints:
    GET    /api/v1/integrations/dead-letter          — List failed queue records
    POST   /api/v1/integrations/dead-letter/requeue  — Requeue failed queue records
    POST   /api/v1/integrations/dead-letter/requeue-embeddings — Requeue failed embedding docs
"""
from __future__ import annotations

import uuid
from typing import Annotated

import httpx
import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from pydantic import BaseModel, Field

from ..auth.dependencies import TenantContext, require_admin, require_viewer
from ..config import settings
from ..db.session import get_db
from ..models.integration import Integration
from ..utils.logging import get_logger
from ..utils.crypto import encrypt_credentials, decrypt_credentials

router = APIRouter()
logger = get_logger(__name__)

SUPPORTED_SOURCES = {
    "slack", "gdrive", "google_drive", "jira", "zendesk", "github", "hubspot",
    "elasticsearch", "datadog", "cloudwatch", "splunk", "azure_monitor", "gcp_logging",
    "railway",
}


# ── Request / Response schemas ────────────────────────────────────────────────
class ConnectRequest(BaseModel):
    source_type: str = Field(
        ...,
        description=(
            "One of: slack | google_drive | jira | zendesk | github | hubspot | "
            "elasticsearch | datadog | cloudwatch | splunk | azure_monitor | gcp_logging | railway"
        ),
    )
    config: dict = Field(default_factory=dict, description="Source-specific config")
    credentials: dict = Field(
        default_factory=dict,
        description="OAuth tokens or API keys (encrypted at rest)",
    )


class IntegrationOut(BaseModel):
    id: str
    source_type: str
    status: str
    last_synced_at: str | None
    total_records: int
    error_message: str | None
    created_at: str


class SyncStatusOut(BaseModel):
    integration_id: str
    source_type: str
    status: str
    last_synced_at: str | None
    total_records: int
    error_message: str | None
    airbyte_connection_id: str | None


# ── List integrations ──────────────────────────────────────────────────────────
@router.get("", response_model=list[IntegrationOut])
async def list_integrations(
    ctx: Annotated[TenantContext, Depends(require_viewer)],
    db=Depends(get_db),
):
    """List all configured data source integrations for this tenant."""
    result = await db.execute(
        sa.select(Integration)
        .where(Integration.tenant_id == ctx.tenant_uuid)
        .order_by(Integration.created_at)
    )
    return [_integration_to_out(i) for i in result.scalars().all()]


# ── Connect new source ─────────────────────────────────────────────────────────
@router.post("", response_model=IntegrationOut, status_code=status.HTTP_201_CREATED)
async def connect_integration(
    body: ConnectRequest,
    ctx: Annotated[TenantContext, Depends(require_admin)],
    background_tasks: BackgroundTasks,
    db=Depends(get_db),
):
    """
    Connect a new data source integration.

    1. Validates source_type.
    2. Encrypts credentials with AES-256.
    3. Creates an Airbyte connection via the Airbyte API.
    4. Persists the integration record.
    5. Triggers an initial background sync.
    """
    if body.source_type not in SUPPORTED_SOURCES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported source type '{body.source_type}'. "
                   f"Supported: {sorted(SUPPORTED_SOURCES)}",
        )

    # Check for existing integration
    existing = await db.execute(
        sa.select(Integration).where(
            Integration.tenant_id == ctx.tenant_uuid,
            Integration.source_type == body.source_type,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail=f"Integration for '{body.source_type}' already exists. Delete it first.",
        )

    # Create Airbyte connection (returns None if Airbyte not configured — that's fine for dev)
    airbyte_connection_id = await _create_airbyte_connection(body.source_type, body.config, body.credentials)

    # Encrypt credentials before storage
    encrypted = encrypt_credentials(body.credentials)

    integration = Integration(
        id=str(uuid.uuid4()),
        tenant_id=ctx.tenant_uuid,
        source_type=body.source_type,
        airbyte_connection_id=airbyte_connection_id,
        status="active",
        credentials=encrypted,
        config=body.config,
        total_records=0,
    )
    db.add(integration)
    await db.commit()
    await db.refresh(integration)

    # Trigger initial sync
    if airbyte_connection_id:
        background_tasks.add_task(_trigger_airbyte_sync, airbyte_connection_id, str(integration.id))
    else:
        # No Airbyte — dispatch to Celery so the sync survives pod restarts
        from apps.worker.tasks.log_source_poller import sync_integration
        sync_integration.delay(str(integration.id), str(ctx.tenant_uuid))
    logger.info("Integration %s created for tenant %s", body.source_type, ctx.tenant_uuid)

    return _integration_to_out(integration)


# ── Disconnect source ──────────────────────────────────────────────────────────
@router.delete("/{integration_id}", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_integration(
    integration_id: str,
    ctx: Annotated[TenantContext, Depends(require_admin)],
    db=Depends(get_db),
):
    """Disconnect an integration and delete its Airbyte connection."""
    integration = await _get_integration_or_404(db, integration_id, ctx.tenant_uuid)

    if integration.airbyte_connection_id:
        await _delete_airbyte_connection(integration.airbyte_connection_id)

    await db.delete(integration)
    await db.commit()
    logger.info("Integration %s disconnected by %s", integration_id, ctx.user_id)


# ── Manual re-sync ─────────────────────────────────────────────────────────────
@router.post("/{integration_id}/sync", status_code=status.HTTP_202_ACCEPTED)
async def trigger_sync(
    integration_id: str,
    ctx: Annotated[TenantContext, Depends(require_admin)],
    background_tasks: BackgroundTasks,
    db=Depends(get_db),
):
    """
    Trigger an immediate manual re-sync.
    - If Airbyte is configured: delegates to Airbyte.
    - If no Airbyte connection (local dev): runs a direct API sync using stored credentials.
    """
    integration = await _get_integration_or_404(db, integration_id, ctx.tenant_uuid)

    if integration.airbyte_connection_id:
        background_tasks.add_task(
            _trigger_airbyte_sync, integration.airbyte_connection_id, integration_id
        )
        return {"message": "Sync triggered via Airbyte", "integration_id": integration_id}

    # No Airbyte — dispatch to Celery so the sync survives pod restarts
    from apps.worker.tasks.log_source_poller import sync_integration
    sync_integration.delay(integration_id, str(ctx.tenant_uuid))
    return {"message": "Sync triggered (direct)", "integration_id": integration_id}


# ── Sync status ────────────────────────────────────────────────────────────────
@router.get("/{integration_id}/status", response_model=SyncStatusOut)
async def get_sync_status(
    integration_id: str,
    ctx: Annotated[TenantContext, Depends(require_admin)],
    db=Depends(get_db),
):
    integration = await _get_integration_or_404(db, integration_id, ctx.tenant_uuid)
    return SyncStatusOut(
        integration_id=str(integration.id),
        source_type=integration.source_type,
        status=integration.status,
        last_synced_at=integration.last_synced_at.isoformat() if integration.last_synced_at else None,
        total_records=integration.total_records or 0,
        error_message=integration.error_message,
        airbyte_connection_id=integration.airbyte_connection_id,
    )


# ── Airbyte webhook (sync-complete callback) ───────────────────────────────────
@router.post("/webhooks/airbyte", include_in_schema=False)
async def airbyte_webhook(
    payload: dict,
    background_tasks: BackgroundTasks,
    db=Depends(get_db),
):
    """
    Receives a POST from Airbyte when a sync completes.
    Triggers the document processing pipeline for the affected tenant + source.

    Payload shape (Airbyte):
        { "connection_id": "...", "status": "succeeded|failed", "records_synced": N }
    """
    from ..services.ingestion_service import trigger_processing_pipeline

    connection_id = payload.get("connection_id")
    sync_status   = payload.get("status")
    records_synced = payload.get("records_synced", 0)

    logger.info("Airbyte webhook: conn=%s status=%s records=%d",
                connection_id, sync_status, records_synced)

    if not connection_id:
        return {"ok": False, "reason": "Missing connection_id"}

    # Find the integration by Airbyte connection ID
    result = await db.execute(
        sa.select(Integration).where(Integration.airbyte_connection_id == connection_id)
    )
    integration = result.scalar_one_or_none()
    if not integration:
        logger.warning("No integration found for Airbyte conn %s", connection_id)
        return {"ok": False, "reason": "Integration not found"}

    if sync_status == "succeeded":
        integration.status = "active"
        integration.total_records = (integration.total_records or 0) + records_synced
        from datetime import datetime, timezone
        integration.last_synced_at = datetime.now(tz=timezone.utc)
        await db.commit()

        # Trigger processing pipeline via Celery
        background_tasks.add_task(
            trigger_processing_pipeline,
            str(integration.tenant_id),
            integration.source_type,
        )
    else:
        integration.status = "error"
        integration.error_message = payload.get("error", "Sync failed")
        await db.commit()

    return {"ok": True}


# ── Helpers / internal clients ─────────────────────────────────────────────────
async def _create_airbyte_connection(
    source_type: str,
    config: dict,
    credentials: dict,
) -> str | None:
    """Create an Airbyte source + connection. Returns connection ID or None if Airbyte unavailable."""
    try:
        async with httpx.AsyncClient(
            base_url=settings.AIRBYTE_API_URL,
            auth=(settings.AIRBYTE_USERNAME, settings.AIRBYTE_PASSWORD),
            timeout=15,
        ) as client:
            # 1. Create source
            source_definition_id = _source_definition_id(source_type)
            if not source_definition_id:
                logger.info("No Airbyte sourceDefinitionId configured for %s; falling back to direct sync", source_type)
                return None

            src_resp = await client.post("/sources", json={
                "name": f"opslens-{source_type}",
                "sourceDefinitionId": source_definition_id,
                "workspaceId": config.get("airbyte_workspace_id", ""),
                "connectionConfiguration": {**config, **credentials},
            })
            src_resp.raise_for_status()
            source_id = src_resp.json()["sourceId"]

            # 2. Create destination (already exists — shared PostgreSQL destination)
            dest_id = config.get("airbyte_destination_id", "")

            # 3. Create connection
            conn_resp = await client.post("/connections", json={
                "sourceId":      source_id,
                "destinationId": dest_id,
                "syncCatalog":   _build_sync_catalog(source_type),
                "scheduleType":  "basic",
                "scheduleData":  {"basicSchedule": {"units": 1, "timeUnit": "HOURS"}},
                "status":        "active",
            })
            conn_resp.raise_for_status()
            return conn_resp.json()["connectionId"]

    except Exception as exc:
        logger.warning("Failed to create Airbyte connection (%s): %s", source_type, exc)
        return None


async def _delete_airbyte_connection(connection_id: str) -> None:
    try:
        async with httpx.AsyncClient(
            base_url=settings.AIRBYTE_API_URL,
            auth=(settings.AIRBYTE_USERNAME, settings.AIRBYTE_PASSWORD),
            timeout=10,
        ) as client:
            await client.delete(f"/connections/{connection_id}")
    except Exception as exc:
        logger.warning("Failed to delete Airbyte connection %s: %s", connection_id, exc)


async def _trigger_airbyte_sync(connection_id: str, integration_id: str) -> None:
    try:
        async with httpx.AsyncClient(
            base_url=settings.AIRBYTE_API_URL,
            auth=(settings.AIRBYTE_USERNAME, settings.AIRBYTE_PASSWORD),
            timeout=10,
        ) as client:
            await client.post("/connections/sync", json={"connectionId": connection_id})
        logger.info("Triggered Airbyte sync for connection %s", connection_id)
    except Exception as exc:
        logger.warning("Failed to trigger sync for %s: %s", connection_id, exc)


def _source_definition_id(source_type: str) -> str:
    """Map source_type to Airbyte sourceDefinitionId."""
    return {
        "slack":    "c2281cee-86f9-4a86-bb48-d23286b4c7bd",
        "jira":     "68e63de2-bb83-4c7e-93fa-a8a9d8a46e95",
        "gdrive":   "78d4e6d9-1000-4736-a35b-9f3e81cca78f",
        "zendesk":  "79c1aa37-dae3-42ae-b066-a00d0ebe4ba5",
        "github":   "ef69ef6e-aa7f-4af1-a01d-ef775033524e",
        "hubspot":  "36c891d9-4bd9-4ac1-b9d2-4d7a0f6b21a1",
        # Log/observability connectors (IDs can vary by Airbyte deployment).
        # Override in env when different from these defaults.
        "elasticsearch": settings.AIRBYTE_SOURCE_DEF_ELASTICSEARCH,
        "datadog":       settings.AIRBYTE_SOURCE_DEF_DATADOG,
        "cloudwatch":    settings.AIRBYTE_SOURCE_DEF_CLOUDWATCH,
        "splunk":        settings.AIRBYTE_SOURCE_DEF_SPLUNK,
        "azure_monitor": settings.AIRBYTE_SOURCE_DEF_AZURE_MONITOR,
        "gcp_logging":   settings.AIRBYTE_SOURCE_DEF_GCP_LOGGING,
    }.get(source_type, "")


def _build_sync_catalog(source_type: str) -> dict:
    """Build minimal Airbyte SyncCatalog for each source type."""
    # Returns the streams we care about, with incremental sync mode
    streams_map = {
        "slack":   ["messages", "channels", "users"],
        "jira":    ["issues", "issue_comments", "projects", "users"],
        "gdrive":  ["files", "activity"],
        "zendesk": ["tickets", "comments", "users"],
        "github":  ["commits", "pull_requests", "issues"],
        "hubspot": ["deals", "contacts", "companies"],
        # Common stream names for log/observability connectors.
        "elasticsearch": ["logs"],
        "datadog":       ["logs"],
        "cloudwatch":    ["logs"],
        "splunk":        ["events"],
        "azure_monitor": ["logs"],
        "gcp_logging":   ["logs"],
    }
    streams = streams_map.get(source_type, [])
    return {
        "streams": [
            {
                "stream": {"name": s, "namespace": "airbyte_staging"},
                "config": {
                    "syncMode": "incremental",
                    "destinationSyncMode": "append_dedup",
                    "selected": True,
                },
            }
            for s in streams
        ]
    }


# ── Dead-letter queue endpoints ────────────────────────────────────────────────

class DeadLetterItem(BaseModel):
    id: str
    source_type: str
    retry_count: int
    max_retries: int
    error_msg: str | None
    failed_at: str
    created_at: str


class RequeueRequest(BaseModel):
    ids: list[str] = Field(
        default_factory=list,
        description="Specific queue record IDs to requeue. Empty = requeue all for this tenant.",
    )
    source_type: str | None = Field(
        None,
        description="Filter by source type when requeueing all (ignored when ids provided).",
    )


class RequeueEmbeddingsRequest(BaseModel):
    ids: list[str] = Field(
        default_factory=list,
        description="Specific canonical_document IDs to requeue. Empty = requeue all failed for this tenant.",
    )
    source_type: str | None = Field(
        None,
        description="Filter by source_type when requeueing all failed embeddings.",
    )


@router.get("/dead-letter", response_model=list[DeadLetterItem])
async def list_dead_letter(
    ctx: Annotated[TenantContext, Depends(require_admin)],
    db=Depends(get_db),
    source_type: str | None = None,
    limit: int = 100,
):
    """
    List ingestion queue records that permanently failed (failed_at IS NOT NULL).
    These are records that hit their retry cap during normalisation.
    Use POST /dead-letter/requeue to reset and retry them.
    """
    filters = "AND source_type = :src" if source_type else ""
    rows = (await db.execute(
        sa.text(f"""
            SELECT id, source_type, retry_count, max_retries,
                   error_msg, failed_at, created_at
            FROM opslens.ingestion_queue
            WHERE tenant_id = :tid
              AND failed_at IS NOT NULL
              {filters}
            ORDER BY failed_at DESC
            LIMIT :lim
        """),
        {"tid": str(ctx.tenant_uuid), "src": source_type, "lim": limit},
    )).fetchall()

    return [
        DeadLetterItem(
            id=str(r.id),
            source_type=r.source_type,
            retry_count=r.retry_count,
            max_retries=r.max_retries,
            error_msg=r.error_msg,
            failed_at=r.failed_at.isoformat(),
            created_at=r.created_at.isoformat(),
        )
        for r in rows
    ]


@router.post("/dead-letter/requeue", status_code=status.HTTP_202_ACCEPTED)
async def requeue_dead_letter(
    body: RequeueRequest,
    ctx: Annotated[TenantContext, Depends(require_admin)],
    db=Depends(get_db),
):
    """
    Reset dead-lettered ingestion queue records so they will be picked up again
    on the next Beat cycle.

    - Clears failed_at, error_msg, and resets retry_count to 0.
    - Leaves processed_at = NULL so the record re-enters the normal queue.
    - Pass specific IDs to requeue individual records, or omit ids to requeue
      all failed records for this tenant (optionally filtered by source_type).
    """
    if body.ids:
        result = await db.execute(
            sa.text("""
                UPDATE opslens.ingestion_queue
                SET failed_at   = NULL,
                    error_msg   = NULL,
                    retry_count = 0
                WHERE tenant_id = :tid
                  AND id = ANY(:ids::uuid[])
                  AND failed_at IS NOT NULL
                RETURNING id
            """),
            {"tid": str(ctx.tenant_uuid), "ids": body.ids},
        )
    else:
        src_filter = "AND source_type = :src" if body.source_type else ""
        result = await db.execute(
            sa.text(f"""
                UPDATE opslens.ingestion_queue
                SET failed_at   = NULL,
                    error_msg   = NULL,
                    retry_count = 0
                WHERE tenant_id = :tid
                  AND failed_at IS NOT NULL
                  {src_filter}
                RETURNING id
            """),
            {"tid": str(ctx.tenant_uuid), "src": body.source_type},
        )

    requeued = [str(r.id) for r in result.fetchall()]
    await db.commit()
    logger.info(
        "Dead-letter requeue: %d records reset by %s (tenant=%s)",
        len(requeued), ctx.user_id, ctx.tenant_uuid,
    )
    return {"requeued": len(requeued), "ids": requeued}


@router.post("/dead-letter/requeue-embeddings", status_code=status.HTTP_202_ACCEPTED)
async def requeue_failed_embeddings(
    body: RequeueEmbeddingsRequest,
    ctx: Annotated[TenantContext, Depends(require_admin)],
    db=Depends(get_db),
):
    """
    Reset canonical_documents with embedding_status='failed' back to 'pending'
    and re-dispatch process_document Celery tasks for each one.

    Use this when a transient OpenAI or Qdrant outage caused permanent failures
    that you now want to retry.

    - Pass specific document IDs, or omit to requeue all failed docs for this tenant.
    - Optionally filter by source_type.
    """
    from apps.worker.tasks.ingestion import process_document

    if body.ids:
        result = await db.execute(
            sa.text("""
                UPDATE opslens.canonical_documents
                SET embedding_status  = 'pending',
                    processing_error  = NULL,
                    updated_at        = now()
                WHERE tenant_id = :tid
                  AND id = ANY(:ids::uuid[])
                  AND embedding_status = 'failed'
                RETURNING id, tenant_id
            """),
            {"tid": str(ctx.tenant_uuid), "ids": body.ids},
        )
    else:
        src_filter = "AND source_type = :src" if body.source_type else ""
        result = await db.execute(
            sa.text(f"""
                UPDATE opslens.canonical_documents
                SET embedding_status  = 'pending',
                    processing_error  = NULL,
                    updated_at        = now()
                WHERE tenant_id = :tid
                  AND embedding_status = 'failed'
                  {src_filter}
                RETURNING id, tenant_id
            """),
            {"tid": str(ctx.tenant_uuid), "src": body.source_type},
        )

    rows = result.fetchall()
    await db.commit()

    for row in rows:
        process_document.delay(str(row.id), str(row.tenant_id))

    logger.info(
        "Requeued %d failed embeddings for tenant=%s by %s",
        len(rows), ctx.tenant_uuid, ctx.user_id,
    )
    return {"requeued": len(rows), "ids": [str(r.id) for r in rows]}


async def _get_integration_or_404(db, integration_id: str, tenant_id: str) -> Integration:
    result = await db.execute(
        sa.select(Integration).where(
            Integration.id == integration_id,
            Integration.tenant_id == tenant_id,
        )
    )
    integration = result.scalar_one_or_none()
    if not integration:
        raise HTTPException(status_code=404, detail="Integration not found")
    return integration


def _integration_to_out(i: Integration) -> IntegrationOut:
    return IntegrationOut(
        id=str(i.id),
        source_type=i.source_type,
        status=i.status,
        last_synced_at=i.last_synced_at.isoformat() if i.last_synced_at else None,
        total_records=i.total_records or 0,
        error_message=i.error_message,
        created_at=i.created_at.isoformat(),
    )
