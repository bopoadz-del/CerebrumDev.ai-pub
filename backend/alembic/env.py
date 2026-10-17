"""Alembic environment — accounts DB.

URL and libpq client-cert hygiene come from ``app.core.accounts_store`` so
boot migrations use the same Neon/TLS settings as the running app:
- ``ACCOUNTS_DATABASE_URL`` (Postgres; ``postgres://`` is upgraded to the
  psycopg driver) when set,
- otherwise sqlite at ``ACCOUNTS_DB_PATH`` or ``STORAGE_PATH/accounts.db``.

The schema itself is imported from the application at migration time, so the
migration can never drift from the code that writes the data.
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Make `app` importable whether alembic runs from the repo (backend/) or the
# production image (/app).
_HERE = Path(__file__).resolve()
for candidate in (_HERE.parents[1], _HERE.parents[2]):
    if (candidate / "app").is_dir():
        sys.path.insert(0, str(candidate))
        break

from app.core.accounts_store import (  # noqa: E402 — path insert above
    _database_url,
    prepare_libpq_client_env,
)

# Non-root image + HOME=/root made libpq open /root/.postgresql/postgresql.crt
# (EACCES) during `alembic upgrade head` and abort the deploy.
prepare_libpq_client_env()

config = context.config

# CLI `alembic upgrade` may configure its own loggers. In-process callers
# (tests via apply_accounts_migrations) must keep the process logging
# config — fileConfig() replaces root handlers and empties pytest caplog.
if config.config_file_name is not None and not config.attributes.get(
    "skip_logging_config"
):
    fileConfig(config.config_file_name)


config.set_main_option("sqlalchemy.url", _database_url())

target_metadata = None


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
