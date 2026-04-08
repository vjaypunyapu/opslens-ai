"""
Storage optimisation migration
- idx_ingestion_queue_processed_at  : speeds up daily staging cleanup DELETE
- idx_canonical_docs_log_source_ts  : speeds up log-source canonical_documents cleanup
- idx_canonical_docs_source_created : composite for tenant + source_type + date range queries

Revision ID: 0004_storage_optimisation
Revises: 0003
"""
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── ingestion_queue ───────────────────────────────────────────────────────
    # The daily retention task deletes processed rows by processed_at.
    # Without an index this is a full table scan on what can be millions of rows.
    op.execute("""
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_ingestion_queue_processed_at
        ON opslens.ingestion_queue (processed_at)
        WHERE processed_at IS NOT NULL
    """)

    # ── canonical_documents ───────────────────────────────────────────────────
    # Speeds up the log-source canonical_documents cleanup query which filters
    # by tenant_id, source_type IN (...log types...), and source_created_at.
    op.execute("""
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_canonical_docs_log_cleanup
        ON opslens.canonical_documents (tenant_id, source_created_at)
        WHERE source_type IN (
            'elasticsearch','datadog','cloudwatch','gcp','splunk','azuremonitor'
        )
    """)

    # ── ingestion_queue: error tracking ──────────────────────────────────────
    # Helps surface stuck/failed records in the queue (error_msg IS NOT NULL).
    op.execute("""
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_ingestion_queue_errors
        ON opslens.ingestion_queue (tenant_id, created_at)
        WHERE processed_at IS NULL AND error_msg IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS opslens.idx_ingestion_queue_processed_at")
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS opslens.idx_canonical_docs_log_cleanup")
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS opslens.idx_ingestion_queue_errors")
