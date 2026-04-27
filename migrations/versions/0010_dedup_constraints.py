"""Add deduplication constraints to canonical_documents.

Q4 fix: replace read-before-write dedup with atomic ON CONFLICT upsert.

Changes:
  1. Drop the too-broad (tenant_id, content_hash) unique constraint — it
     conflates "same content" with "same document", causing false collisions
     when two different source_ids happen to produce identical text.
  2. Add (tenant_id, source_type, source_id) unique constraint — this is the
     true identity key: one row per external document per tenant.
  3. Add (tenant_id, source_type, source_id, content_hash) partial-unique
     index to support the "skip if unchanged" fast-path without a SELECT.

With these constraints, ingestion.py can use:
    INSERT ... ON CONFLICT (tenant_id, source_type, source_id)
    DO UPDATE SET content_hash = EXCLUDED.content_hash, ...
    WHERE canonical_documents.content_hash != EXCLUDED.content_hash

...which is atomic and race-condition-free.

Revision ID: 0010
Revises: 0009
"""
from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Drop the old (tenant_id, content_hash) unique constraint.
    #    It was too broad — same content from different sources collides.
    op.drop_constraint(
        "uq_canonical_documents_hash",
        "canonical_documents",
        schema="opslens",
        type_="unique",
    )

    # 2. Add the correct identity key: one row per source document per tenant.
    #    This is what enables atomic ON CONFLICT upserts in ingestion.py.
    op.create_unique_constraint(
        "uq_canonical_documents_source",
        "canonical_documents",
        ["tenant_id", "source_type", "source_id"],
        schema="opslens",
    )

    # 3. Add a non-unique index on content_hash for the "skip if unchanged"
    #    fast-path (WHERE content_hash = :ch on an upsert DO NOTHING branch).
    op.create_index(
        "idx_canonical_documents_content_hash",
        "canonical_documents",
        ["tenant_id", "content_hash"],
        schema="opslens",
    )


def downgrade() -> None:
    op.drop_index(
        "idx_canonical_documents_content_hash",
        table_name="canonical_documents",
        schema="opslens",
    )
    op.drop_constraint(
        "uq_canonical_documents_source",
        "canonical_documents",
        schema="opslens",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_canonical_documents_hash",
        "canonical_documents",
        ["tenant_id", "content_hash"],
        schema="opslens",
    )
