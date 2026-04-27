"""Add cooldown_minutes to AlertRoutingRule and acknowledged to AlertHistory.

Q10 fix: per-routing-rule cooldown (replaces global LOG_FAST_ALERT_COOLDOWN_MINUTES).
Q11 fix: acknowledged flag on AlertHistory so PagerDuty escalation can be cancelled.

Revision ID: 0011
Revises: 0010
"""
from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Use raw SQL with IF NOT EXISTS throughout — these columns may already exist
    # if the always-run main.py patches ran before this migration.
    op.execute("ALTER TABLE opslens.alert_routing_rules ADD COLUMN IF NOT EXISTS cooldown_minutes INTEGER NOT NULL DEFAULT 10")
    op.execute("ALTER TABLE opslens.alert_history ADD COLUMN IF NOT EXISTS acknowledged BOOLEAN NOT NULL DEFAULT false")
    op.execute("ALTER TABLE opslens.alert_history ADD COLUMN IF NOT EXISTS acknowledged_at TIMESTAMPTZ")
    op.execute("ALTER TABLE opslens.alert_history ADD COLUMN IF NOT EXISTS acknowledged_by VARCHAR(255)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_alert_history_acknowledged ON opslens.alert_history (tenant_id, acknowledged)")


def downgrade() -> None:
    op.drop_index("idx_alert_history_acknowledged", table_name="alert_history", schema="opslens")
    op.drop_column("alert_history", "acknowledged_by", schema="opslens")
    op.drop_column("alert_history", "acknowledged_at", schema="opslens")
    op.drop_column("alert_history", "acknowledged", schema="opslens")
    op.drop_column("alert_routing_rules", "cooldown_minutes", schema="opslens")
