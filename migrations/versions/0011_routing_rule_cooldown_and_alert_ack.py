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
    # Q10: per-routing-rule alert cooldown
    op.add_column(
        "alert_routing_rules",
        sa.Column(
            "cooldown_minutes",
            sa.Integer(),
            nullable=False,
            server_default="10",   # 10 min default — matches previous global setting
        ),
        schema="opslens",
    )

    # Q11: acknowledgement fields on AlertHistory
    op.add_column(
        "alert_history",
        sa.Column("acknowledged", sa.Boolean(), nullable=False, server_default="false"),
        schema="opslens",
    )
    op.add_column(
        "alert_history",
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        schema="opslens",
    )
    op.add_column(
        "alert_history",
        sa.Column("acknowledged_by", sa.String(255), nullable=True),
        schema="opslens",
    )
    # Index for fast lookup in the escalation task
    op.create_index(
        "idx_alert_history_acknowledged",
        "alert_history",
        ["tenant_id", "acknowledged"],
        schema="opslens",
    )


def downgrade() -> None:
    op.drop_index("idx_alert_history_acknowledged", table_name="alert_history", schema="opslens")
    op.drop_column("alert_history", "acknowledged_by", schema="opslens")
    op.drop_column("alert_history", "acknowledged_at", schema="opslens")
    op.drop_column("alert_history", "acknowledged", schema="opslens")
    op.drop_column("alert_routing_rules", "cooldown_minutes", schema="opslens")
