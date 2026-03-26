"""
Enterprise features migration
- billing_subscriptions table (Stripe subscription state)
- usage_events table (metering ledger)
- alert_routing_rules.team_id column (RBAC team ownership)

Revision ID: 0002_enterprise_features
Revises: 0001_initial_schema
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── usage_events ──────────────────────────────────────────────────────────
    op.create_table(
        "usage_events",
        sa.Column("id",                     sa.String(),    nullable=False),
        sa.Column("tenant_id",              sa.String(),    nullable=False),
        sa.Column("event_type",             sa.String(),    nullable=False),
        sa.Column("resource_id",            sa.String(),    nullable=True),
        sa.Column("actor_id",               sa.String(),    nullable=True),
        sa.Column("quantity",               sa.Integer(),   nullable=False, server_default="1"),
        sa.Column("unit_cost_usd",          sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column("total_cost_usd",         sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column("stripe_meter_event_id",  sa.String(),    nullable=True),
        sa.Column("stripe_reported_at",     sa.DateTime(timezone=True), nullable=True),
        sa.Column("occurred_at",            sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("extra",                  postgresql.JSONB(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        schema="opslens",
    )
    op.create_index("ix_usage_events_tenant_id",   "usage_events", ["tenant_id"],  schema="opslens")
    op.create_index("ix_usage_events_event_type",  "usage_events", ["event_type"], schema="opslens")
    op.create_index("ix_usage_events_occurred_at", "usage_events", ["occurred_at"],schema="opslens")

    # ── billing_subscriptions ────────────────────────────────────────────────
    op.create_table(
        "billing_subscriptions",
        sa.Column("id",                     sa.String(),    nullable=False),
        sa.Column("tenant_id",              sa.String(),    nullable=False),
        sa.Column("stripe_customer_id",     sa.String(),    nullable=True),
        sa.Column("stripe_subscription_id", sa.String(),    nullable=True),
        sa.Column("plan",                   sa.String(),    nullable=False, server_default="free"),
        sa.Column("status",                 sa.String(),    nullable=False, server_default="trialing"),
        sa.Column("seats_limit",            sa.Integer(),   nullable=False, server_default="3"),
        sa.Column("docs_limit_per_month",   sa.Integer(),   nullable=False, server_default="500"),
        sa.Column("alerts_limit_per_month", sa.Integer(),   nullable=False, server_default="100"),
        sa.Column("trial_ends_at",          sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_period_start",   sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_period_end",     sa.DateTime(timezone=True), nullable=True),
        sa.Column("canceled_at",            sa.DateTime(timezone=True), nullable=True),
        sa.Column("pending_docs",           sa.Integer(),   nullable=False, server_default="0"),
        sa.Column("pending_alerts",         sa.Integer(),   nullable=False, server_default="0"),
        sa.Column("pending_briefs",         sa.Integer(),   nullable=False, server_default="0"),
        sa.Column("pending_rag_queries",    sa.Integer(),   nullable=False, server_default="0"),
        sa.Column("created_at",             sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at",             sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", name="uq_billing_subscriptions_tenant_id"),
        schema="opslens",
    )
    op.create_index("ix_billing_subscriptions_tenant_id",           "billing_subscriptions", ["tenant_id"],              schema="opslens")
    op.create_index("ix_billing_subscriptions_stripe_customer_id",  "billing_subscriptions", ["stripe_customer_id"],     schema="opslens")
    op.create_index("ix_billing_subscriptions_stripe_subscription_id", "billing_subscriptions", ["stripe_subscription_id"], schema="opslens")

    # ── alert_routing_rules.team_id column ───────────────────────────────────
    op.add_column(
        "alert_routing_rules",
        sa.Column("team_id", sa.String(), nullable=True),
        schema="opslens",
    )
    op.create_index("ix_alert_routing_rules_team_id", "alert_routing_rules", ["team_id"], schema="opslens")


def downgrade() -> None:
    op.drop_index("ix_alert_routing_rules_team_id", table_name="alert_routing_rules", schema="opslens")
    op.drop_column("alert_routing_rules", "team_id", schema="opslens")

    op.drop_index("ix_billing_subscriptions_stripe_subscription_id", table_name="billing_subscriptions", schema="opslens")
    op.drop_index("ix_billing_subscriptions_stripe_customer_id",     table_name="billing_subscriptions", schema="opslens")
    op.drop_index("ix_billing_subscriptions_tenant_id",              table_name="billing_subscriptions", schema="opslens")
    op.drop_table("billing_subscriptions", schema="opslens")

    op.drop_index("ix_usage_events_occurred_at", table_name="usage_events", schema="opslens")
    op.drop_index("ix_usage_events_event_type",  table_name="usage_events", schema="opslens")
    op.drop_index("ix_usage_events_tenant_id",   table_name="usage_events", schema="opslens")
    op.drop_table("usage_events", schema="opslens")
