"""Add dead-letter queue columns that shipped in the ORM/schema.sql but were
never migrated onto the actual database.

canonical_documents.processing_error / processing_attempts and the
ingestion_queue retry columns were added to apps/api/db/models.py and
documented as idempotent ALTER TABLE statements in schema.sql, but no
Alembic revision was ever created for them. Since production applies
schema changes exclusively via Alembic (apps/api/main.py only runs its
dev-mode patches when settings.ENV != "production"), every insert into
canonical_documents in production has been failing with
UndefinedColumnError on "processing_error".

Revision ID: 0012
Revises: 0011
"""
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE opslens.canonical_documents "
        "ADD COLUMN IF NOT EXISTS processing_error TEXT"
    )
    op.execute(
        "ALTER TABLE opslens.canonical_documents "
        "ADD COLUMN IF NOT EXISTS processing_attempts INTEGER NOT NULL DEFAULT 0"
    )

    op.execute(
        "ALTER TABLE opslens.ingestion_queue "
        "ADD COLUMN IF NOT EXISTS retry_count INTEGER NOT NULL DEFAULT 0"
    )
    op.execute(
        "ALTER TABLE opslens.ingestion_queue "
        "ADD COLUMN IF NOT EXISTS max_retries INTEGER NOT NULL DEFAULT 10"
    )
    op.execute(
        "ALTER TABLE opslens.ingestion_queue "
        "ADD COLUMN IF NOT EXISTS next_retry_at TIMESTAMPTZ"
    )
    op.execute(
        "ALTER TABLE opslens.ingestion_queue "
        "ADD COLUMN IF NOT EXISTS failed_at TIMESTAMPTZ"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_ingestion_queue_failed "
        "ON opslens.ingestion_queue (tenant_id, failed_at DESC) "
        "WHERE failed_at IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_index("idx_ingestion_queue_failed", table_name="ingestion_queue", schema="opslens")
    op.drop_column("ingestion_queue", "failed_at", schema="opslens")
    op.drop_column("ingestion_queue", "next_retry_at", schema="opslens")
    op.drop_column("ingestion_queue", "max_retries", schema="opslens")
    op.drop_column("ingestion_queue", "retry_count", schema="opslens")
    op.drop_column("canonical_documents", "processing_attempts", schema="opslens")
    op.drop_column("canonical_documents", "processing_error", schema="opslens")
