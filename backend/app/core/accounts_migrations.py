"""Apply accounts-DB Alembic revisions.

Alembic is the schema source of truth. Production runs
``python -m alembic upgrade head`` at container boot. Tests that need a
column missing from an old fixture must call :func:`apply_accounts_migrations`
— they must not rely on request-path ``ALTER TABLE``.
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config


def _alembic_ini() -> Path:
    here = Path(__file__).resolve()
    for candidate in (here.parents[2] / "alembic.ini", Path("/app/alembic.ini")):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("alembic.ini not found (looked next to backend/ and /app)")


def apply_accounts_migrations() -> None:
    """Upgrade the accounts URL (env-resolved) to Alembic head.

    Not a request-path helper. Call from tests or ops after pointing
    ``ACCOUNTS_DATABASE_URL`` / ``ACCOUNTS_DB_PATH`` at the target database.
    """
    ini = _alembic_ini()
    cfg = Config(str(ini))
    cfg.set_main_option("script_location", str(ini.parent / "alembic"))
    cfg.attributes["skip_logging_config"] = True
    command.upgrade(cfg, "head")
