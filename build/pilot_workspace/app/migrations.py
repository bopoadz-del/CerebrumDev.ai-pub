"""Apply and roll back Alembic revisions for this platform.

Deploy (scripts/entrypoint.sh) and FastAPI lifespan both call
upgrade_head() against STORAGE_PATH. Failure refuses boot.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.store import connect, db_path

ROOT = Path(__file__).resolve().parents[1]


def alembic_config() -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    return cfg


def upgrade_head() -> str | None:
    command.upgrade(alembic_config(), "head")
    return current_revision()


def upgrade_to(revision: str) -> str | None:
    command.upgrade(alembic_config(), revision)
    return current_revision()


def downgrade(revision: str) -> str | None:
    command.downgrade(alembic_config(), revision)
    return current_revision()


def current_revision() -> str | None:
    if not db_path().exists():
        return None
    conn = connect()
    try:
        row = conn.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
        return str(row[0]) if row else None
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()


def head_revision() -> str | None:
    from alembic.script import ScriptDirectory

    return ScriptDirectory.from_config(alembic_config()).get_current_head()
