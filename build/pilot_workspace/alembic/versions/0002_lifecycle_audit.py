"""v2 schema change: lifecycle_audit table (up and down).

Revision ID: 0002_lifecycle_audit
Revises: 0001_baseline
Create Date: 2026-08-23
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0002_lifecycle_audit"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "lifecycle_audit",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("event", sa.Text(), nullable=False),
        sa.Column("at", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("lifecycle_audit")
