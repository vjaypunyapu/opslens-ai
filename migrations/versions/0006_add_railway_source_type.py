"""
Expand integrations source_type CHECK constraint to include railway,
google_drive, gcp_logging, and azure_monitor.

Previous migration (0005) added log sources using the short alias keys
('gcp', 'azuremonitor') that the original fetchers used.  The UI and
fetchers now use the canonical full names ('gcp_logging', 'azure_monitor')
plus a new 'google_drive' alias (UI sends google_drive, gdrive is legacy)
and the new 'railway' integration.

This migration:
1. Drops the existing CHECK constraint.
2. Rebuilds it with all current source type values (both aliases kept for
   backwards compat with any existing rows that have the old short names).
3. Rebuilds the partial index for log source polling to include the new types.

Revision ID: 0006_add_railway_source_type
Revises: 0005
"""
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

_ALL_SOURCE_TYPES = (
    # Contextual / knowledge sources
    "slack", "gdrive", "google_drive", "jira", "zendesk", "github", "hubspot",
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

_CONSTRAINT_NAME = "integrations_source_type_check"
_INDEX_NAME = "idx_integrations_log_sources_active"


def upgrade() -> None:
    # 1. Drop old constraint
    op.execute(
        f"ALTER TABLE opslens.integrations DROP CONSTRAINT IF EXISTS {_CONSTRAINT_NAME}"
    )

    # 2. Rebuild constraint with all current source types
    types_sql = ", ".join(f"'{t}'" for t in _ALL_SOURCE_TYPES)
    op.execute(
        f"ALTER TABLE opslens.integrations "
        f"ADD CONSTRAINT {_CONSTRAINT_NAME} "
        f"CHECK (source_type IN ({types_sql}))"
    )

    # 3. Rebuild partial index to include new log source types
    op.execute(f"DROP INDEX IF EXISTS opslens.{_INDEX_NAME}")
    log_types_sql = ", ".join(f"'{t}'" for t in _LOG_SOURCE_TYPES)
    op.execute(f"""
        CREATE INDEX IF NOT EXISTS {_INDEX_NAME}
        ON opslens.integrations (tenant_id, source_type, last_synced_at)
        WHERE status = 'active'
          AND source_type IN ({log_types_sql})
    """)


def downgrade() -> None:
    # Restore the 0005 state
    op.execute(f"DROP INDEX IF EXISTS opslens.{_INDEX_NAME}")
    op.execute(
        f"ALTER TABLE opslens.integrations DROP CONSTRAINT IF EXISTS {_CONSTRAINT_NAME}"
    )

    old_types = (
        "slack", "gdrive", "jira", "zendesk", "github", "hubspot",
        "elasticsearch", "datadog", "cloudwatch", "gcp", "splunk", "azuremonitor",
    )
    old_types_sql = ", ".join(f"'{t}'" for t in old_types)
    op.execute(
        f"ALTER TABLE opslens.integrations "
        f"ADD CONSTRAINT {_CONSTRAINT_NAME} "
        f"CHECK (source_type IN ({old_types_sql}))"
    )

    old_log_types = ("elasticsearch", "datadog", "cloudwatch", "gcp", "splunk", "azuremonitor")
    old_log_sql = ", ".join(f"'{t}'" for t in old_log_types)
    op.execute(f"""
        CREATE INDEX IF NOT EXISTS {_INDEX_NAME}
        ON opslens.integrations (tenant_id, source_type, last_synced_at)
        WHERE status = 'active'
          AND source_type IN ({old_log_sql})
    """)
