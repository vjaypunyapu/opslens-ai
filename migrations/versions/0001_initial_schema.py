"""Initial schema — creates all OpsLens AI tables.

Revision ID: 0001
Revises:
Create Date: 2026-01-01 00:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Extensions & Schema ──────────────────────────────────────────────────
    op.execute("CREATE SCHEMA IF NOT EXISTS opslens")
    op.execute("CREATE SCHEMA IF NOT EXISTS airbyte_staging")
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gin")

    # ── tenants ──────────────────────────────────────────────────────────────
    op.create_table(
        "tenants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(100), unique=True, nullable=False),
        sa.Column("plan", sa.String(50), nullable=False, server_default="starter"),
        sa.Column("settings", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema="opslens",
    )

    # ── users ────────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("opslens.tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("external_id", sa.String(255), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255)),
        sa.Column("role", sa.String(50), nullable=False, server_default="member"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema="opslens",
    )
    op.create_index("idx_users_tenant_id", "users", ["tenant_id"], schema="opslens")
    op.create_index("idx_users_external_id", "users", ["external_id"], schema="opslens")

    # ── integrations ─────────────────────────────────────────────────────────
    op.create_table(
        "integrations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("opslens.tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_type", sa.String(50), nullable=False),
        sa.Column("airbyte_connection_id", sa.String(255)),
        sa.Column("airbyte_source_id", sa.String(255)),
        sa.Column("credentials", postgresql.JSONB),
        sa.Column("status", sa.String(50), nullable=False, server_default="pending"),
        sa.Column("last_synced_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "source_type", name="uq_integrations_tenant_source"),
        schema="opslens",
    )

    # ── canonical_documents ───────────────────────────────────────────────────
    op.create_table(
        "canonical_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("opslens.tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_type", sa.String(50), nullable=False),
        sa.Column("source_id", sa.String(512), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("title", sa.Text),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("author", sa.String(255)),
        sa.Column("url", sa.Text),
        sa.Column("doc_metadata", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("embedding_status", sa.String(50), nullable=False, server_default="pending"),
        sa.Column("source_created_at", sa.DateTime(timezone=True)),
        sa.Column("source_updated_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "content_hash", name="uq_canonical_documents_hash"),
        schema="opslens",
    )
    op.create_index("idx_docs_tenant_source", "canonical_documents",
                    ["tenant_id", "source_type"], schema="opslens")
    op.create_index("idx_docs_embedding_status", "canonical_documents",
                    ["embedding_status"], schema="opslens")

    # ── insights ─────────────────────────────────────────────────────────────
    op.create_table(
        "insights",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("opslens.tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("insight_type", sa.String(100), nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("summary", sa.Text, nullable=False),
        sa.Column("magnitude", sa.String(20), nullable=False, server_default="medium"),
        sa.Column("confidence", sa.Numeric(3, 2)),
        sa.Column("status", sa.String(50), nullable=False, server_default="active"),
        sa.Column("source_types", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("evidence", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("snoozed_until", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema="opslens",
    )
    op.create_index("idx_insights_tenant_status", "insights",
                    ["tenant_id", "status"], schema="opslens")

    # ── alert_rules ───────────────────────────────────────────────────────────
    op.create_table(
        "alert_rules",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("opslens.tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("conditions", postgresql.JSONB, nullable=False),
        sa.Column("channels", postgresql.JSONB, nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("cooldown_minutes", sa.Integer, nullable=False, server_default="60"),
        sa.Column("last_triggered_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema="opslens",
    )

    # ── alert_history ─────────────────────────────────────────────────────────
    op.create_table(
        "alert_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("rule_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("opslens.alert_rules.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trigger_data", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("channels_notified", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("triggered_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema="opslens",
    )

    # ── chat_sessions ─────────────────────────────────────────────────────────
    op.create_table(
        "chat_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("opslens.tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("opslens.users.id", ondelete="SET NULL")),
        sa.Column("title", sa.String(512)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema="opslens",
    )

    # ── chat_messages ─────────────────────────────────────────────────────────
    op.create_table(
        "chat_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("session_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("opslens.chat_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("sources", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("token_count", sa.Integer),
        sa.Column("latency_ms", sa.Integer),
        sa.Column("feedback", sa.String(20)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema="opslens",
    )
    op.create_index("idx_chat_messages_session", "chat_messages",
                    ["session_id", "created_at"], schema="opslens")

    # ── audit_logs ────────────────────────────────────────────────────────────
    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True)),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("resource_type", sa.String(100)),
        sa.Column("resource_id", sa.String(255)),
        sa.Column("details", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("ip_address", sa.String(45)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema="opslens",
    )

    # ── airbyte_staging.processing_state ──────────────────────────────────────
    op.create_table(
        "processing_state",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("connection_id", sa.String(255), nullable=False),
        sa.Column("source_type", sa.String(50), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="pending"),
        sa.Column("records_total", sa.Integer, server_default="0"),
        sa.Column("records_processed", sa.Integer, server_default="0"),
        sa.Column("error_message", sa.Text),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema="airbyte_staging",
    )


def downgrade() -> None:
    op.drop_table("processing_state", schema="airbyte_staging")
    op.drop_table("audit_logs", schema="opslens")
    op.drop_table("chat_messages", schema="opslens")
    op.drop_table("chat_sessions", schema="opslens")
    op.drop_table("alert_history", schema="opslens")
    op.drop_table("alert_rules", schema="opslens")
    op.drop_table("insights", schema="opslens")
    op.drop_table("canonical_documents", schema="opslens")
    op.drop_table("integrations", schema="opslens")
    op.drop_table("users", schema="opslens")
    op.drop_table("tenants", schema="opslens")
    op.execute("DROP SCHEMA IF EXISTS airbyte_staging CASCADE")
    op.execute("DROP SCHEMA IF EXISTS opslens CASCADE")
