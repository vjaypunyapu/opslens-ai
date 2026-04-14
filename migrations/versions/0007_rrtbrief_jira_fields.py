"""
Add jira_ticket_key and jira_ticket_url columns to rrt_briefs.

Allows RRT briefs to be pushed to Jira directly from the OpsLens UI.
The columns store the created ticket key (e.g. OPS-42) and full URL
so the brief detail panel can link directly to the ticket.

Revision ID: 0007_rrtbrief_jira_fields
Revises: 0006
"""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "rrt_briefs",
        sa.Column("jira_ticket_key", sa.String(), nullable=True),
        schema="opslens",
    )
    op.add_column(
        "rrt_briefs",
        sa.Column("jira_ticket_url", sa.String(), nullable=True),
        schema="opslens",
    )


def downgrade() -> None:
    op.drop_column("rrt_briefs", "jira_ticket_url", schema="opslens")
    op.drop_column("rrt_briefs", "jira_ticket_key", schema="opslens")
