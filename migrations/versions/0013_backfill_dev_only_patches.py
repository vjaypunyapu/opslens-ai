"""Formally migrate everything that has only ever been applied via
apps/api/main.py's dev-only lifespan block (`if settings.ENV != "production"`).

That block has been silently keeping production's schema alive — chunk_count,
source_id, the RBAC tables (teams/team_members/team_resource_permissions/
pending_invites), insights' redesigned columns, alert_rules, integrations,
chat_messages, users.updated_at — none of which have ever had an Alembic
revision. It only works because ENV is not actually set to the literal string
"production" in this deployment; if that ever gets corrected (or a fresh
environment is spun up with ENV=production set correctly, e.g. for this
demo), every one of these columns/tables disappears again — the exact
UndefinedColumnError class of bug already hit twice this week (processing_error
in 0012, and canonical_documents.chunk_count, discovered while auditing for
more of the same).

This migration is a straight, idempotent transcription of that dev-only
block so the schema no longer depends on an ENV-variable accident. It is a
no-op against the current database (everything here already exists via the
main.py patches) and makes a fresh/staging database match production without
relying on ENV being wrong.

Revision ID: 0013
Revises: 0012
"""
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── chat_sessions: nullable fixes ──────────────────────────────────────
    op.execute("ALTER TABLE opslens.chat_sessions ALTER COLUMN user_id DROP NOT NULL")
    op.execute("ALTER TABLE opslens.chat_sessions ALTER COLUMN title DROP NOT NULL")

    # ── chat_messages ───────────────────────────────────────────────────────
    op.execute("ALTER TABLE opslens.chat_messages ADD COLUMN IF NOT EXISTS sources JSONB NOT NULL DEFAULT '[]'")
    op.execute("ALTER TABLE opslens.chat_messages ADD COLUMN IF NOT EXISTS token_count INTEGER")
    op.execute("ALTER TABLE opslens.chat_messages ADD COLUMN IF NOT EXISTS latency_ms INTEGER")
    op.execute("ALTER TABLE opslens.chat_messages ADD COLUMN IF NOT EXISTS feedback VARCHAR(20)")

    # ── insights: redesigned column types (idempotent, only fires if the old
    #    type is still in place) ────────────────────────────────────────────
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'opslens' AND table_name = 'insights'
                  AND column_name = 'confidence' AND data_type != 'numeric'
            ) THEN
                ALTER TABLE opslens.insights DROP COLUMN confidence CASCADE;
                ALTER TABLE opslens.insights ADD COLUMN confidence NUMERIC(3,2);
            END IF;
        END; $$
    """)
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'opslens' AND table_name = 'insights'
                  AND column_name = 'magnitude' AND data_type = 'numeric'
            ) THEN
                ALTER TABLE opslens.insights DROP COLUMN magnitude CASCADE;
                ALTER TABLE opslens.insights
                    ADD COLUMN magnitude VARCHAR(20) NOT NULL DEFAULT 'medium';
            END IF;
        END; $$
    """)
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'opslens' AND table_name = 'insights'
                  AND column_name = 'source_types' AND data_type = 'ARRAY'
            ) THEN
                ALTER TABLE opslens.insights ALTER COLUMN source_types DROP DEFAULT;
                ALTER TABLE opslens.insights
                    ALTER COLUMN source_types TYPE JSONB USING to_jsonb(source_types);
                ALTER TABLE opslens.insights ALTER COLUMN source_types SET NOT NULL;
                ALTER TABLE opslens.insights ALTER COLUMN source_types SET DEFAULT '[]'::jsonb;
            END IF;
        END; $$
    """)
    op.execute("ALTER TABLE opslens.insights ADD COLUMN IF NOT EXISTS source_types JSONB NOT NULL DEFAULT '[]'")
    op.execute("ALTER TABLE opslens.insights ADD COLUMN IF NOT EXISTS evidence JSONB NOT NULL DEFAULT '{}'")
    op.execute("ALTER TABLE opslens.insights ADD COLUMN IF NOT EXISTS generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()")
    op.execute("ALTER TABLE opslens.insights ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ")
    op.execute("ALTER TABLE opslens.insights ADD COLUMN IF NOT EXISTS snoozed_until TIMESTAMPTZ")
    op.execute("ALTER TABLE opslens.insights ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()")
    op.execute("ALTER TABLE opslens.insights ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()")

    # ── users ────────────────────────────────────────────────────────────────
    op.execute("ALTER TABLE opslens.users ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()")

    # ── integrations ─────────────────────────────────────────────────────────
    op.execute("ALTER TABLE opslens.integrations ADD COLUMN IF NOT EXISTS airbyte_connection_id VARCHAR(255)")
    op.execute("ALTER TABLE opslens.integrations ADD COLUMN IF NOT EXISTS airbyte_source_id VARCHAR(255)")
    op.execute("ALTER TABLE opslens.integrations ADD COLUMN IF NOT EXISTS config JSONB")
    op.execute("ALTER TABLE opslens.integrations ADD COLUMN IF NOT EXISTS total_records BIGINT NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE opslens.integrations ADD COLUMN IF NOT EXISTS error_message TEXT")

    # ── alert_rules ──────────────────────────────────────────────────────────
    op.execute("ALTER TABLE opslens.alert_rules ADD COLUMN IF NOT EXISTS description TEXT")
    op.execute("ALTER TABLE opslens.alert_rules ADD COLUMN IF NOT EXISTS conditions JSONB NOT NULL DEFAULT '[]'")
    op.execute("ALTER TABLE opslens.alert_rules ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE")
    op.execute("ALTER TABLE opslens.alert_rules ADD COLUMN IF NOT EXISTS cooldown_minutes INTEGER NOT NULL DEFAULT 60")
    op.execute("ALTER TABLE opslens.alert_rules ADD COLUMN IF NOT EXISTS last_triggered_at TIMESTAMPTZ")
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'opslens' AND table_name = 'alert_rules'
                  AND column_name = 'channels' AND data_type = 'ARRAY'
            ) THEN
                ALTER TABLE opslens.alert_rules ALTER COLUMN channels DROP DEFAULT;
                ALTER TABLE opslens.alert_rules
                    ALTER COLUMN channels TYPE JSONB USING to_jsonb(channels);
                ALTER TABLE opslens.alert_rules ALTER COLUMN channels SET NOT NULL;
                ALTER TABLE opslens.alert_rules ALTER COLUMN channels SET DEFAULT '[]'::jsonb;
            END IF;
        END; $$
    """)

    # ── alert_history ────────────────────────────────────────────────────────
    op.execute("ALTER TABLE opslens.alert_history ADD COLUMN IF NOT EXISTS trigger_data JSONB NOT NULL DEFAULT '{}'")
    op.execute("ALTER TABLE opslens.alert_history ADD COLUMN IF NOT EXISTS channels_notified JSONB NOT NULL DEFAULT '[]'")
    op.execute("ALTER TABLE opslens.alert_history ADD COLUMN IF NOT EXISTS triggered_at TIMESTAMPTZ NOT NULL DEFAULT NOW()")

    # ── canonical_documents: the columns direct_sync_service.py writes on
    #    every single sync — chunk_count in particular has never had a
    #    migration and only exists today via the dev-only main.py patch ──────
    op.execute("ALTER TABLE opslens.canonical_documents ADD COLUMN IF NOT EXISTS source_updated_at TIMESTAMPTZ")
    op.execute("ALTER TABLE opslens.canonical_documents ADD COLUMN IF NOT EXISTS chunk_count INTEGER")
    op.execute("ALTER TABLE opslens.canonical_documents ADD COLUMN IF NOT EXISTS source_id VARCHAR(512)")

    # ── RBAC: teams / team_members / team_resource_permissions / pending_invites
    #    Never had an Alembic revision at all — created only via main.py's
    #    dev-only create_all()+patches. ─────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS opslens.teams (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL REFERENCES opslens.tenants(id) ON DELETE CASCADE,
            name VARCHAR(255) NOT NULL,
            description TEXT,
            created_by VARCHAR(255),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS opslens.team_members (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            team_id UUID NOT NULL REFERENCES opslens.teams(id) ON DELETE CASCADE,
            user_external_id VARCHAR(255) NOT NULL,
            user_email VARCHAR(255),
            role VARCHAR(50) NOT NULL DEFAULT 'member',
            added_by VARCHAR(255),
            added_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_team_members_user UNIQUE (team_id, user_external_id)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_team_members_user_external_id ON opslens.team_members (user_external_id)")
    op.execute("""
        CREATE TABLE IF NOT EXISTS opslens.team_resource_permissions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            team_id UUID NOT NULL REFERENCES opslens.teams(id) ON DELETE CASCADE,
            source_type VARCHAR(50) NOT NULL,
            source_id VARCHAR(512) NOT NULL,
            can_read BOOLEAN NOT NULL DEFAULT TRUE,
            can_see_metrics BOOLEAN NOT NULL DEFAULT FALSE,
            can_see_logs BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_team_resource UNIQUE (team_id, source_type, source_id)
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS opslens.pending_invites (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL REFERENCES opslens.tenants(id) ON DELETE CASCADE,
            team_id UUID REFERENCES opslens.teams(id) ON DELETE SET NULL,
            email VARCHAR(255) NOT NULL,
            role VARCHAR(50) NOT NULL DEFAULT 'member',
            team_role VARCHAR(50) NOT NULL DEFAULT 'member',
            invited_by VARCHAR(255),
            accepted_at TIMESTAMPTZ,
            expires_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_pending_invites_email ON opslens.pending_invites (email)")

    # ── FTS: search_vector + GIN index + trigger ────────────────────────────
    # Already applied unconditionally by main.py's separate "always-run" block
    # regardless of ENV, so this is belt-and-suspenders — but it's the column
    # the hybrid-search half of every RRT brief / chat answer depends on
    # (hybrid_retriever.py), so it gets a real migration too rather than
    # relying solely on that lifespan hook running before the first request.
    op.execute("ALTER TABLE opslens.canonical_documents ADD COLUMN IF NOT EXISTS search_vector tsvector")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_canonical_documents_search_vector "
        "ON opslens.canonical_documents USING GIN(search_vector)"
    )
    op.execute("""
        CREATE OR REPLACE FUNCTION opslens.update_canonical_document_search_vector()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.search_vector := to_tsvector('english',
                coalesce(NEW.title, '') || ' ' || coalesce(NEW.content, '')
            );
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("DROP TRIGGER IF EXISTS trig_canonical_documents_fts ON opslens.canonical_documents")
    op.execute("""
        CREATE TRIGGER trig_canonical_documents_fts
        BEFORE INSERT OR UPDATE OF title, content
        ON opslens.canonical_documents
        FOR EACH ROW EXECUTE FUNCTION opslens.update_canonical_document_search_vector()
    """)
    op.execute("""
        UPDATE opslens.canonical_documents
        SET search_vector = to_tsvector('english', coalesce(title, '') || ' ' || coalesce(content, ''))
        WHERE search_vector IS NULL
    """)


def downgrade() -> None:
    # Intentionally a no-op. This migration only backfills state that was
    # already live in production via main.py's dev-only patches; reversing it
    # would drop columns/tables that other, older code paths still depend on
    # regardless of migration state.
    pass
