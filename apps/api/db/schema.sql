-- ============================================================================
-- OpsLens AI — Production PostgreSQL Schema
-- ============================================================================
--
-- Design principles:
--   • Multi-tenancy via tenant_id column on every table (RLS-ready)
--   • UUID v4 primary keys via gen_random_uuid() (pgcrypto)
--   • All timestamps in UTC (TIMESTAMPTZ)
--   • JSONB for flexible source-specific metadata
--   • pg_trgm for fuzzy title/content search
--   • Airbyte writes raw data to the `airbyte_staging` schema;
--     workers read from staging and write to the canonical schema below
--
-- Run via Alembic in production.
-- Use `scripts/migrate.sh` as a wrapper.
-- ============================================================================

-- ── Extensions ────────────────────────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS "pgcrypto";       -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS "pg_trgm";        -- fuzzy text search (GIN index)
CREATE EXTENSION IF NOT EXISTS "btree_gin";      -- composite GIN indexes
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";      -- uuid_generate_v4() compatibility

-- ── Schemas ───────────────────────────────────────────────────────────────────
CREATE SCHEMA IF NOT EXISTS airbyte_staging;     -- Airbyte raw data destination
CREATE SCHEMA IF NOT EXISTS opslens;             -- All application tables

SET search_path = opslens, public;


-- ============================================================================
-- CORE: TENANTS
-- ============================================================================
CREATE TABLE tenants (
    id          UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT          NOT NULL,
    slug        TEXT          UNIQUE NOT NULL  -- subdomain: slug.opslens.ai
                              CHECK (slug ~ '^[a-z0-9-]+$'),
    plan        TEXT          NOT NULL DEFAULT 'starter'
                              CHECK (plan IN ('starter', 'growth', 'enterprise', 'inactive')),
    timezone    TEXT          NOT NULL DEFAULT 'UTC',
    settings    JSONB         NOT NULL DEFAULT '{}'::jsonb,
    -- Limits (overridable per plan)
    max_integrations  INTEGER NOT NULL DEFAULT 3,
    max_users         INTEGER NOT NULL DEFAULT 5,
    max_chat_sessions INTEGER NOT NULL DEFAULT 100,
    created_at  TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE tenants IS 'Top-level multi-tenant isolation unit. One row per customer organisation.';
COMMENT ON COLUMN tenants.slug IS 'URL-safe identifier used in subdomain routing.';
COMMENT ON COLUMN tenants.settings IS 'Tenant-level configuration overrides (feature flags, branding, etc.).';


-- ============================================================================
-- CORE: USERS
-- ============================================================================
CREATE TABLE users (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    external_id     TEXT        UNIQUE NOT NULL,  -- Clerk/Auth0 subject claim
    email           TEXT        NOT NULL,
    name            TEXT,
    role            TEXT        NOT NULL DEFAULT 'member'
                                CHECK (role IN ('admin', 'member', 'viewer')),
    avatar_url      TEXT,
    last_active_at  TIMESTAMPTZ,
    invited_by      UUID        REFERENCES users(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_users_tenant   ON users(tenant_id);
CREATE INDEX idx_users_external ON users(external_id);

COMMENT ON TABLE users IS 'Platform users. Authenticated via Clerk/Auth0 JWT.';
COMMENT ON COLUMN users.external_id IS 'JWT subject claim from Clerk/Auth0. Used for token→user lookup.';
COMMENT ON COLUMN users.role IS 'RBAC role: admin = full access, member = read+write, viewer = read-only.';


-- ============================================================================
-- INTEGRATIONS (Airbyte-backed data sources)
-- ============================================================================
CREATE TABLE integrations (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    source_type     TEXT        NOT NULL
                                CHECK (source_type IN ('slack', 'gdrive', 'jira', 'zendesk', 'github', 'hubspot')),
    airbyte_conn_id TEXT,                  -- Airbyte Connection UUID
    status          TEXT        NOT NULL DEFAULT 'pending'
                                CHECK (status IN ('pending', 'active', 'error', 'paused', 'disconnected')),
    credentials     JSONB,                 -- AES-256-GCM encrypted OAuth tokens / API keys
    config          JSONB,                 -- Source-specific config (workspace IDs, repo lists, etc.)
    last_synced_at  TIMESTAMPTZ,
    next_sync_at    TIMESTAMPTZ,
    total_records   BIGINT      NOT NULL DEFAULT 0,
    error_message   TEXT,
    error_count     INTEGER     NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, source_type)        -- one active integration per source per tenant
);

CREATE INDEX idx_integrations_tenant ON integrations(tenant_id);
CREATE INDEX idx_integrations_status ON integrations(status) WHERE status IN ('error', 'pending');

COMMENT ON TABLE integrations IS 'Configured data source integrations. One per (tenant, source_type).';
COMMENT ON COLUMN integrations.credentials IS 'Stored as AES-256 ciphertext. Never expose in API responses.';
COMMENT ON COLUMN integrations.config IS 'E.g. {"slack_workspace_id": "T01...", "jira_project_keys": ["PROD","INFRA"]}';


-- ============================================================================
-- CANONICAL DOCUMENTS (normalised records from all sources)
-- ============================================================================
CREATE TABLE canonical_documents (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    integration_id      UUID        REFERENCES integrations(id) ON DELETE SET NULL,

    -- Source identification
    source_type         TEXT        NOT NULL,   -- slack | gdrive | jira | zendesk | github | hubspot
    source_id           TEXT        NOT NULL,   -- original record ID in the source system
    content_hash        TEXT        NOT NULL,   -- SHA-256(source_type:source_id:content) for dedup

    -- Content
    title               TEXT,
    content             TEXT,                   -- full extracted plain text
    author              TEXT,
    url                 TEXT,                   -- deep link back to source record

    -- Source-specific metadata (varies by source_type)
    doc_metadata        JSONB       NOT NULL DEFAULT '{}'::jsonb,
    /*
        Slack:   { channel, channel_id, thread_ts, reactions, workspace }
        Jira:    { status, priority, issue_type, labels, assignee, epic, sprint, project_key }
        Google Drive: { mime_type, size_bytes, parent_folder, shared_with, version }
        Zendesk: { ticket_id, priority, status, tags, organization_name, satisfaction_rating }
        GitHub:  { repo, branch, event_type, pr_number, commit_sha, files_changed }
        HubSpot: { deal_stage, amount, close_date, owner, company_name, contact_email }
    */

    -- Processing state
    embedding_status    TEXT        NOT NULL DEFAULT 'pending'
                                    CHECK (embedding_status IN ('pending', 'processing', 'done', 'failed')),
    chunk_count         INTEGER     NOT NULL DEFAULT 0,
    processing_error    TEXT,
    processing_attempts INTEGER     NOT NULL DEFAULT 0,

    -- Source timestamps (preserved for temporal queries)
    source_created_at   TIMESTAMPTZ,
    source_updated_at   TIMESTAMPTZ,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (tenant_id, source_type, source_id)
);

-- Core lookup indexes
CREATE INDEX idx_docs_tenant       ON canonical_documents(tenant_id);
CREATE INDEX idx_docs_source_type  ON canonical_documents(tenant_id, source_type);
CREATE INDEX idx_docs_hash         ON canonical_documents(content_hash);

-- Pipeline queue: only pending/failed records that need processing
CREATE INDEX idx_docs_embed_pending ON canonical_documents(tenant_id, source_created_at DESC)
    WHERE embedding_status IN ('pending', 'failed');

-- Temporal range queries (insight engine looks back N days)
CREATE INDEX idx_docs_source_ts ON canonical_documents(tenant_id, source_type, source_created_at DESC);

-- Fuzzy title / content search
CREATE INDEX idx_docs_title_trgm   ON canonical_documents USING GIN (title gin_trgm_ops);

-- JSONB path queries (e.g. filter by Jira status, Zendesk priority)
CREATE INDEX idx_docs_metadata ON canonical_documents USING GIN (doc_metadata);

COMMENT ON TABLE canonical_documents IS
    'Central normalised document store. All source records are mapped to this schema before embedding.';
COMMENT ON COLUMN canonical_documents.content_hash IS
    'SHA-256 dedup key. Records with unchanged content are not re-embedded.';
COMMENT ON COLUMN canonical_documents.doc_metadata IS
    'Source-specific fields preserved in JSONB. Indexed for filtered queries.';


-- ============================================================================
-- SLACK-SPECIFIC DENORMALISED VIEW
-- (for fast Slack-only queries — avoids full JSONB scan)
-- ============================================================================
CREATE TABLE slack_messages (
    document_id     UUID        PRIMARY KEY REFERENCES canonical_documents(id) ON DELETE CASCADE,
    tenant_id       UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    message_ts      TEXT        NOT NULL,       -- Slack timestamp (also sort key)
    channel_id      TEXT,
    channel_name    TEXT,
    user_id         TEXT,
    thread_ts       TEXT,                       -- parent thread timestamp if reply
    reaction_count  INTEGER     NOT NULL DEFAULT 0,
    has_files       BOOLEAN     NOT NULL DEFAULT FALSE,
    is_bot          BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_slack_tenant_channel ON slack_messages(tenant_id, channel_id, message_ts DESC);
CREATE INDEX idx_slack_thread         ON slack_messages(thread_ts) WHERE thread_ts IS NOT NULL;

COMMENT ON TABLE slack_messages IS 'Denormalised Slack metadata for channel-based filtering.';


-- ============================================================================
-- JIRA-SPECIFIC DENORMALISED VIEW
-- ============================================================================
CREATE TABLE jira_issues (
    document_id     UUID        PRIMARY KEY REFERENCES canonical_documents(id) ON DELETE CASCADE,
    tenant_id       UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    issue_key       TEXT        NOT NULL,       -- e.g. PROD-441
    project_key     TEXT,
    issue_type      TEXT,                       -- Bug | Story | Task | Epic | Sub-task
    status          TEXT,                       -- To Do | In Progress | In Review | Done
    priority        TEXT,                       -- Blocker | Critical | Major | Minor | Trivial
    assignee_name   TEXT,
    reporter_name   TEXT,
    story_points    NUMERIC,
    sprint_name     TEXT,
    labels          TEXT[],
    issue_created_at TIMESTAMPTZ,
    issue_updated_at TIMESTAMPTZ,
    resolved_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_jira_tenant_status   ON jira_issues(tenant_id, status);
CREATE INDEX idx_jira_tenant_priority ON jira_issues(tenant_id, priority);
CREATE INDEX idx_jira_assignee        ON jira_issues(tenant_id, assignee_name);
CREATE INDEX idx_jira_stale           ON jira_issues(tenant_id, issue_updated_at)
    WHERE status IN ('In Progress', 'In Review', 'Blocked');

COMMENT ON TABLE jira_issues IS 'Denormalised Jira issue metadata for bottleneck and bug-trend queries.';


-- ============================================================================
-- ZENDESK-SPECIFIC DENORMALISED VIEW
-- ============================================================================
CREATE TABLE zendesk_tickets (
    document_id         UUID        PRIMARY KEY REFERENCES canonical_documents(id) ON DELETE CASCADE,
    tenant_id           UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    ticket_id           BIGINT,
    status              TEXT,                   -- new | open | pending | hold | solved | closed
    priority            TEXT,                   -- low | normal | high | urgent
    ticket_type         TEXT,                   -- problem | incident | question | task
    organization_name   TEXT,
    requester_email     TEXT,
    assignee_name       TEXT,
    tags                TEXT[],
    satisfaction_rating TEXT,                   -- good | bad | unoffered
    first_reply_minutes INTEGER,
    resolution_minutes  INTEGER,
    ticket_created_at   TIMESTAMPTZ,
    ticket_solved_at    TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_zendesk_tenant_status   ON zendesk_tickets(tenant_id, status);
CREATE INDEX idx_zendesk_tenant_priority ON zendesk_tickets(tenant_id, priority);
CREATE INDEX idx_zendesk_org             ON zendesk_tickets(tenant_id, organization_name);
CREATE INDEX idx_zendesk_created         ON zendesk_tickets(tenant_id, ticket_created_at DESC);

COMMENT ON TABLE zendesk_tickets IS 'Denormalised Zendesk ticket metadata for complaint and churn analysis.';


-- ============================================================================
-- GITHUB-SPECIFIC DENORMALISED VIEW
-- ============================================================================
CREATE TABLE github_events (
    document_id     UUID        PRIMARY KEY REFERENCES canonical_documents(id) ON DELETE CASCADE,
    tenant_id       UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    repo_name       TEXT,
    event_type      TEXT,                       -- push | pull_request | release | deployment
    branch_name     TEXT,
    tag_name        TEXT,                       -- populated for release events
    commit_sha      TEXT,
    pr_number       INTEGER,
    author_login    TEXT,
    files_changed   INTEGER,
    additions       INTEGER,
    deletions       INTEGER,
    is_release      BOOLEAN     NOT NULL DEFAULT FALSE,
    event_at        TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_github_tenant_repo    ON github_events(tenant_id, repo_name, event_at DESC);
CREATE INDEX idx_github_releases       ON github_events(tenant_id, event_at DESC) WHERE is_release = TRUE;

COMMENT ON TABLE github_events IS 'Denormalised GitHub event metadata for release correlation analysis.';


-- ============================================================================
-- AIRBYTE STAGING SCHEMA
-- (Airbyte writes here; workers read + normalise into canonical_documents)
-- ============================================================================

-- Processing state tracker (owned by our workers, not Airbyte)
CREATE TABLE airbyte_staging.processing_state (
    table_name      TEXT,
    tenant_id       UUID,
    last_processed  TIMESTAMPTZ NOT NULL DEFAULT '1970-01-01 00:00:00+00',
    records_processed BIGINT NOT NULL DEFAULT 0,
    last_error      TEXT,
    PRIMARY KEY (table_name, tenant_id)
);

-- Airbyte appends _airbyte_raw_id, _airbyte_extracted_at, _airbyte_loaded_at, _airbyte_data
-- to every destination table. Example destination table (created by Airbyte automatically):
/*
    airbyte_staging.slack_messages
    airbyte_staging.slack_channels
    airbyte_staging.jira_issues
    airbyte_staging.jira_projects
    airbyte_staging.gdrive_files
    airbyte_staging.zendesk_tickets
    airbyte_staging.zendesk_users
    airbyte_staging.github_commits
    airbyte_staging.github_pull_requests
    airbyte_staging.github_releases
    airbyte_staging.hubspot_deals
    airbyte_staging.hubspot_contacts
*/


-- ============================================================================
-- INSIGHTS
-- ============================================================================
CREATE TABLE insights (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    insight_type        TEXT        NOT NULL
                                    CHECK (insight_type IN (
                                        'complaint_spike', 'feature_trend',
                                        'release_correlation', 'eng_bottleneck', 'churn_risk'
                                    )),
    title               TEXT        NOT NULL,
    summary             TEXT        NOT NULL,
    magnitude           NUMERIC,               -- % change, count, or severity score
    confidence          TEXT        NOT NULL DEFAULT 'medium'
                                    CHECK (confidence IN ('high', 'medium', 'low')),
    status              TEXT        NOT NULL DEFAULT 'active'
                                    CHECK (status IN ('active', 'resolved', 'snoozed')),
    source_types        TEXT[]      NOT NULL DEFAULT '{}'::text[],
    supporting_doc_ids  UUID[]      DEFAULT '{}'::uuid[],
    raw_data            JSONB       NOT NULL DEFAULT '{}'::jsonb,   -- LLM raw output
    generated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at         TIMESTAMPTZ,
    snoozed_until       TIMESTAMPTZ,
    resolved_by         UUID        REFERENCES users(id) ON DELETE SET NULL
);

CREATE INDEX idx_insights_tenant      ON insights(tenant_id, generated_at DESC);
CREATE INDEX idx_insights_type        ON insights(tenant_id, insight_type, generated_at DESC);
CREATE INDEX idx_insights_status      ON insights(tenant_id, status) WHERE status = 'active';
CREATE INDEX idx_insights_snoozed     ON insights(snoozed_until) WHERE status = 'snoozed';

COMMENT ON TABLE insights IS 'AI-generated operational insights. Created by Celery Beat insight detectors.';
COMMENT ON COLUMN insights.magnitude IS
    'Quantitative measure of insight severity/size. Context-dependent: % change for spikes, count for trends.';
COMMENT ON COLUMN insights.raw_data IS 'Full LLM JSON response including confidence and intermediate reasoning.';


-- ============================================================================
-- ALERT RULES
-- ============================================================================
CREATE TABLE alert_rules (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    created_by      UUID        REFERENCES users(id) ON DELETE SET NULL,
    name            TEXT        NOT NULL,
    insight_types   TEXT[]      NOT NULL,
    condition       JSONB       NOT NULL,
    /*
        condition schema:
        {
          "field":    "magnitude" | "insight_type" | "title",
          "operator": "gt" | "gte" | "lt" | "lte" | "eq" | "contains",
          "value":    <number or string>
        }
    */
    channels        TEXT[]      NOT NULL DEFAULT '{}'::text[],
    channel_config  JSONB       NOT NULL DEFAULT '{}'::jsonb,
    /*
        channel_config schema:
        {
          "slack_webhook_url":  "https://hooks.slack.com/...",
          "email_recipients":   ["oncall@company.com"]
        }
    */
    cooldown_hours  INTEGER     NOT NULL DEFAULT 4 CHECK (cooldown_hours >= 1),
    last_fired_at   TIMESTAMPTZ,
    enabled         BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_alert_rules_tenant  ON alert_rules(tenant_id, enabled) WHERE enabled = TRUE;

COMMENT ON TABLE alert_rules IS
    'User-defined alerting rules. Evaluated by Celery Beat every 15 minutes.';


-- ============================================================================
-- ALERT HISTORY
-- ============================================================================
CREATE TABLE alert_history (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    rule_id         UUID        REFERENCES alert_rules(id) ON DELETE SET NULL,
    insight_id      UUID        REFERENCES insights(id) ON DELETE SET NULL,
    channels_sent   TEXT[]      NOT NULL DEFAULT '{}'::text[],
    payload         JSONB,                 -- full message payload that was dispatched
    fired_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    delivery_status TEXT        NOT NULL DEFAULT 'sent'
                                CHECK (delivery_status IN ('sent', 'failed', 'partial'))
);

CREATE INDEX idx_alert_history_tenant ON alert_history(tenant_id, fired_at DESC);
CREATE INDEX idx_alert_history_rule   ON alert_history(rule_id, fired_at DESC);

COMMENT ON TABLE alert_history IS 'Immutable log of every alert dispatch. Used for audit and dedup.';


-- ============================================================================
-- CHAT SESSIONS
-- ============================================================================
CREATE TABLE chat_sessions (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id     UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title       TEXT,                      -- auto-set from first user message
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_chat_sessions_user ON chat_sessions(user_id, updated_at DESC);

COMMENT ON TABLE chat_sessions IS 'A single conversation thread in the AI chat interface.';


-- ============================================================================
-- CHAT MESSAGES
-- ============================================================================
CREATE TABLE chat_messages (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id          UUID        NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    role                TEXT        NOT NULL CHECK (role IN ('user', 'assistant')),
    content             TEXT        NOT NULL,
    source_doc_ids      UUID[]      DEFAULT '{}'::uuid[],  -- Qdrant chunks retrieved for this response
    feedback            SMALLINT    CHECK (feedback IN (-1, 1)),   -- thumbs down / up
    -- Token usage (for cost tracking)
    prompt_tokens       INTEGER,
    completion_tokens   INTEGER,
    total_tokens        INTEGER GENERATED ALWAYS AS (
        COALESCE(prompt_tokens, 0) + COALESCE(completion_tokens, 0)
    ) STORED,
    latency_ms          INTEGER,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_chat_messages_session ON chat_messages(session_id, created_at);
CREATE INDEX idx_chat_messages_feedback ON chat_messages(feedback) WHERE feedback IS NOT NULL;

COMMENT ON TABLE chat_messages IS 'Individual messages within a chat session.';
COMMENT ON COLUMN chat_messages.source_doc_ids IS
    'References to canonical_documents used as RAG context for this response.';
COMMENT ON COLUMN chat_messages.feedback IS
    'User thumbs up/down feedback. Synced to LangSmith for RAG evaluation.';


-- ============================================================================
-- AUDIT LOG
-- ============================================================================
CREATE TABLE audit_logs (
    id          BIGSERIAL   PRIMARY KEY,
    tenant_id   UUID        NOT NULL,
    user_id     UUID,
    action      TEXT        NOT NULL,
    /*
        action examples:
          "chat.query"          "chat.session.create"
          "insight.resolve"     "insight.snooze"
          "alert.create"        "alert.test_fire"
          "integration.connect" "integration.disconnect"
          "user.invite"         "user.role_change"
    */
    resource_type TEXT,                    -- insight | alert_rule | integration | user
    resource_id   TEXT,
    metadata      JSONB       DEFAULT '{}'::jsonb,
    ip_address    INET,
    user_agent    TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_audit_tenant ON audit_logs(tenant_id, created_at DESC);
CREATE INDEX idx_audit_user   ON audit_logs(user_id, created_at DESC) WHERE user_id IS NOT NULL;
CREATE INDEX idx_audit_action ON audit_logs(action, created_at DESC);

COMMENT ON TABLE audit_logs IS
    'Immutable append-only audit trail for all user and system actions.';


-- ============================================================================
-- FUNCTIONS & TRIGGERS
-- ============================================================================

-- Auto-update updated_at columns
CREATE OR REPLACE FUNCTION opslens.set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Apply trigger to all tables with updated_at
CREATE TRIGGER trg_tenants_updated_at
    BEFORE UPDATE ON tenants
    FOR EACH ROW EXECUTE FUNCTION opslens.set_updated_at();

CREATE TRIGGER trg_integrations_updated_at
    BEFORE UPDATE ON integrations
    FOR EACH ROW EXECUTE FUNCTION opslens.set_updated_at();

CREATE TRIGGER trg_canonical_documents_updated_at
    BEFORE UPDATE ON canonical_documents
    FOR EACH ROW EXECUTE FUNCTION opslens.set_updated_at();

CREATE TRIGGER trg_alert_rules_updated_at
    BEFORE UPDATE ON alert_rules
    FOR EACH ROW EXECUTE FUNCTION opslens.set_updated_at();

CREATE TRIGGER trg_chat_sessions_updated_at
    BEFORE UPDATE ON chat_sessions
    FOR EACH ROW EXECUTE FUNCTION opslens.set_updated_at();


-- Auto-wake snoozed insights (call periodically or use CHECK on select)
CREATE OR REPLACE FUNCTION opslens.wake_snoozed_insights()
RETURNS INTEGER AS $$
DECLARE
    updated_count INTEGER;
BEGIN
    UPDATE insights
    SET status = 'active', snoozed_until = NULL
    WHERE status = 'snoozed'
      AND snoozed_until <= NOW();
    GET DIAGNOSTICS updated_count = ROW_COUNT;
    RETURN updated_count;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION opslens.wake_snoozed_insights IS
    'Reactivates insights whose snooze period has expired. Call from Celery Beat every 15 minutes.';


-- ============================================================================
-- VIEWS (convenience queries)
-- ============================================================================

-- Active insights per tenant, enriched with source breakdown
CREATE VIEW opslens.v_active_insights AS
SELECT
    i.id,
    i.tenant_id,
    i.insight_type,
    i.title,
    i.summary,
    i.magnitude,
    i.confidence,
    i.source_types,
    i.generated_at,
    i.raw_data -> 'confidence' AS llm_confidence,
    -- Days since generation
    EXTRACT(EPOCH FROM (NOW() - i.generated_at)) / 86400 AS age_days
FROM insights i
WHERE i.status = 'active'
ORDER BY i.generated_at DESC;

-- Embedding pipeline queue
CREATE VIEW opslens.v_embedding_queue AS
SELECT
    id,
    tenant_id,
    source_type,
    title,
    LEFT(content, 100) AS content_preview,
    processing_attempts,
    created_at
FROM canonical_documents
WHERE embedding_status IN ('pending', 'failed')
  AND processing_attempts < 3
ORDER BY created_at;

-- Token usage per tenant (last 30 days)
CREATE VIEW opslens.v_token_usage AS
SELECT
    cs.tenant_id,
    DATE_TRUNC('day', cm.created_at) AS usage_date,
    SUM(cm.total_tokens)    AS total_tokens,
    SUM(cm.prompt_tokens)   AS prompt_tokens,
    SUM(cm.completion_tokens) AS completion_tokens,
    COUNT(*)                AS message_count
FROM chat_messages cm
JOIN chat_sessions cs ON cm.session_id = cs.id
WHERE cm.created_at >= NOW() - INTERVAL '30 days'
GROUP BY cs.tenant_id, DATE_TRUNC('day', cm.created_at)
ORDER BY cs.tenant_id, usage_date;


-- ============================================================================
-- ROW LEVEL SECURITY (enable in production)
-- ============================================================================
-- Enable RLS on all tenant-scoped tables.
-- ── Dead-letter queue columns (idempotent migrations) ────────────────────────
-- Run these once against any existing database to add DLQ tracking columns.
-- New deployments get these columns via CREATE TABLE above (ingestion_queue is
-- created by SQLAlchemy create_all, not this file).

ALTER TABLE opslens.canonical_documents
    ADD COLUMN IF NOT EXISTS processing_error    TEXT,
    ADD COLUMN IF NOT EXISTS processing_attempts INTEGER NOT NULL DEFAULT 0;

-- ingestion_queue is managed by SQLAlchemy; these are provided for existing DBs
-- where the table was already created without the DLQ columns.
DO $$ BEGIN
    ALTER TABLE opslens.ingestion_queue ADD COLUMN IF NOT EXISTS retry_count  INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE opslens.ingestion_queue ADD COLUMN IF NOT EXISTS max_retries  INTEGER NOT NULL DEFAULT 5;
    ALTER TABLE opslens.ingestion_queue ADD COLUMN IF NOT EXISTS failed_at    TIMESTAMPTZ;
EXCEPTION WHEN undefined_table THEN NULL; END $$;

-- Index for efficient DLQ queries: "show me everything that permanently failed"
CREATE INDEX IF NOT EXISTS idx_ingestion_queue_failed
    ON opslens.ingestion_queue(tenant_id, failed_at DESC)
    WHERE failed_at IS NOT NULL;

-- Application sets the current tenant via: SET LOCAL opslens.current_tenant = '<uuid>';

/*
ALTER TABLE canonical_documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE insights             ENABLE ROW LEVEL SECURITY;
ALTER TABLE alert_rules          ENABLE ROW LEVEL SECURITY;
ALTER TABLE chat_sessions        ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON canonical_documents
    USING (tenant_id = current_setting('opslens.current_tenant')::uuid);

-- Repeat for all tables above.
*/
