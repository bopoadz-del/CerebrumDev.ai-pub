"""Baseline: accounts schema as defined by app.core.accounts_store.

Imports the application's own SQLAlchemy metadata so the baseline can never
drift from the code that writes the data. ``checkfirst=True`` makes this a
no-op on databases that already have the tables (every deployment that
predates Alembic) — those are effectively stamped by running this revision.

Fresh databases:   alembic upgrade head
Existing databases: alembic upgrade head  (no-op, records the revision)
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    from app.core.accounts_store import _META

    bind = op.get_bind()
    _META.create_all(bind=bind, checkfirst=True)


def downgrade() -> None:
    # Baseline: never drop user/account data.
    pass
