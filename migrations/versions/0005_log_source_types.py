"""
Add log source types to integrations CHECK constraint and integrations table.

The integrations table currently only allows contextual source types
(slack, gdrive, jira, zendesk, github, hubspot) in its source_type CHECK.
This migration drops the old constraint and adds a new one that also includes
all log source types (elasticsearch, datadog, cloudwatch, gcp, splunk,
azuremonitor) so customers can connect log systems via the UI.

Revision ID: 0005_log_source_types
Revises: 0004
"""
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

_ALL_SOURCE_TYPES = (
    # Contextual / knowledge sources
    "slack", "gdrive", "jira", "zendesk", "github", "hubspot",
    # Log / observability sources
    "elasticsearch", "datadog", "cloudwatch", "gcp", "splunk", "azuremonitor",
)

_CONSTRAINT_NAME = "integrations_source_type_check"


def upgrade() -> None:
    # Drop the old restrictive CHECK constraint
    op.execute(f"ALTER TABLE opslens.integrations DROP CONSTRAINT IF EXISTS {_CONSTRAINT_NAME}")

    # Add updated CHECK that includes log source types
    types_sql = ", ".join(f"'{t}'" for t in _ALL_SOURCE_TYPES)
    op.execute(
        f"ALTER TABLE opslens.integrations "
        f"ADD CONSTRAINT {_CONSTRAINT_NAME} "
        f"CHECK (source_type IN ({types_sql}))"
    )

    # Add a partial index so queries for active log source integrations are fast
    # (used by poll_all_log_sources every 5 minutes)
    op.execute("""
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_integrations_log_sources_active
        ON opslens.integrations (tenant_id, source_type, last_synced_at)
        WHERE status = 'active'
          AND source_type IN (
              'elasticsearch','datadog','cloudwatch','gcp','splunk','azuremonitor'
          )
    """)


def downgrade() -> None:
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_integrations_log_sources_active")
    op.execute(f"ALTER TABLE opslens.integrations DROP CONSTRAINT IF EXISTS {_CONSTRAINT_NAME}")

    # Restore original narrow constraint
    op.execute(
        f"ALTER TABLE opslens.integrations "
        f"ADD CONSTRAINT {_CONSTRAINT_NAME} "
        f"CHECK (source_type IN ('slack','gdrive','jira','zendesk','github','hubspot'))"
    )
