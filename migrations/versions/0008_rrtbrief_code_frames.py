"""Add code_frames column to rrt_briefs for GitHub source-code context

Revision ID: 0008
Revises: 0007
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "rrt_briefs",
        sa.Column("code_frames", JSONB, nullable=True, server_default="[]"),
        schema="opslens",
    )


def downgrade() -> None:
    op.drop_column("rrt_briefs", "code_frames", schema="opslens")
