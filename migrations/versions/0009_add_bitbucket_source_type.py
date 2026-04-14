"""Add bitbucket to integrations source_type CHECK constraint.

Revision ID: 0009
Revises: 0008
"""
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

_CONSTRAINT_NAME = "integrations_source_type_check"
_INDEX_NAME = "idx_integrations_log_sources_active"

_ALL_SOURCE_TYPES = (
    # Contextual / knowledge sources
    "slack", "gdrive", "google_drive", "jira", "zendesk",
    "github", "bitbucket", "hubspot",
    # Log / observability sources — canonical names
    "elasticsearch", "datadog", "cloudwatch",
    "gcp_logging", "splunk", "azure_monitor",
    # Legacy short aliases kept for existing rows
    "gcp", "azuremonitor",
    # New: Railway deployment logs
    "railway",
)

_LOG_SOURCE_TYPES = (
    "elasticsearch", "datadog", "cloudwatch",
    "gcp_logging", "gcp",
    "splunk",
    "azure_monitor", "azuremonitor",
    "railway",
)


def upgrade() -> None:
    # 1. Drop old constraint
    op.execute(
        f"ALTER TABLE opslens.integrations DROP CONSTRAINT IF EXISTS {_CONSTRAINT_NAME}"
    )

    # 2. Rebuild with bitbucket included
    types_sql = ", ".join(f"'{t}'" for t in _ALL_SOURCE_TYPES)
    op.execute(
        f"ALTER TABLE opslens.integrations "
        f"ADD CONSTRAINT {_CONSTRAINT_NAME} "
        f"CHECK (source_type IN ({types_sql}))"
    )

    # 3. Rebuild partial index (unchanged log sources, but re-create for safety)
    op.execute(f"DROP INDEX IF EXISTS opslens.{_INDEX_NAME}")
    log_types_sql = ", ".join(f"'{t}'" for t in _LOG_SOURCE_TYPES)
    op.execute(f"""
        CREATE INDEX IF NOT EXISTS {_INDEX_NAME}
        ON opslens.integrations (tenant_id, source_type, last_synced_at)
        WHERE status = 'active'
          AND source_type IN ({log_types_sql})
    """)


def downgrade() -> None:
    # Restore 0006 state (without bitbucket)
    op.execute(f"DROP INDEX IF EXISTS opslens.{_INDEX_NAME}")
    op.execute(
        f"ALTER TABLE opslens.integrations DROP CONSTRAINT IF EXISTS {_CONSTRAINT_NAME}"
    )

    old_types = (
        "slack", "gdrive", "google_drive", "jira", "zendesk", "github", "hubspot",
        "elasticsearch", "datadog", "cloudwatch",
        "gcp_logging", "splunk", "azure_monitor",
        "gcp", "azuremonitor",
        "railway",
    )
    old_types_sql = ", ".join(f"'{t}'" for t in old_types)
    op.execute(
        f"ALTER TABLE opslens.integrations "
        f"ADD CONSTRAINT {_CONSTRAINT_NAME} "
        f"CHECK (source_type IN ({old_types_sql}))"
    )

    log_types_sql = ", ".join(f"'{t}'" for t in _LOG_SOURCE_TYPES)
    op.execute(f"""
        CREATE INDEX IF NOT EXISTS {_INDEX_NAME}
        ON opslens.integrations (tenant_id, source_type, last_synced_at)
        WHERE status = 'active'
          AND source_type IN ({log_types_sql})
    """)
