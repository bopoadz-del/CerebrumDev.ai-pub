"""Columns that used to be patched at runtime by ``_ensure_column``.

Alembic is the schema source of truth. Fresh databases already receive these
from 0001's metadata ``create_all``. Existing databases that predate the
billing/reset columns must pick them up here — not from a request-path
``ALTER TABLE``.
"""

from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

_COLUMNS = (
    ("reset_token_hash", sa.String(128)),
    ("reset_expires_at", sa.String(64)),
    ("trial_ends_at", sa.String(64)),
    ("subscription_status", sa.String(32)),
    ("stripe_customer_id", sa.String(128)),
    ("stripe_subscription_id", sa.String(128)),
)


def upgrade() -> None:
    bind = op.get_bind()
    tables = sa.inspect(bind).get_table_names()
    if "accounts" not in tables:
        return
    existing = {c["name"] for c in sa.inspect(bind).get_columns("accounts")}
    for name, col_type in _COLUMNS:
        if name not in existing:
            op.add_column("accounts", sa.Column(name, col_type, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    tables = sa.inspect(bind).get_table_names()
    if "accounts" not in tables:
        return
    existing = {c["name"] for c in sa.inspect(bind).get_columns("accounts")}
    for name, _col_type in reversed(_COLUMNS):
        if name in existing:
            op.drop_column("accounts", name)
