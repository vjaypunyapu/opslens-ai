from __future__ import annotations


async def trigger_processing_pipeline(tenant_id: str, source_type: str) -> None:
    """Dispatch process_staging_batch to normalise records queued by Airbyte."""
    from apps.worker.tasks.ingestion import process_staging_batch
    process_staging_batch.delay(tenant_id, source_type)
