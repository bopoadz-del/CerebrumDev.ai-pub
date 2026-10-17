"""Usage counters table for server-side trial quotas (Phase 2.5).

Reversible: ``upgrade`` creates ``usage_counters``; ``downgrade`` drops it.
Split out of the baseline ``create_all`` so the trial-quota schema has an
explicit, reversible revision independent of the application metadata.
"""

from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "usage_counters" in sa.inspect(bind).get_table_names():
        return  # baseline create_all on an old DB may have made it already
    op.create_table(
        "usage_counters",
        sa.Column("account_id", sa.String(64), primary_key=True),
        sa.Column("counter", sa.String(64), primary_key=True),
        sa.Column("period", sa.String(32), primary_key=True),
        sa.Column("value", sa.Integer, nullable=False, server_default="0"),
        sa.Column("updated_at", sa.String(64), nullable=False),
    )


def downgrade() -> None:
    bind = op.get_bind()
    if "usage_counters" in sa.inspect(bind).get_table_names():
        op.drop_table("usage_counters")
