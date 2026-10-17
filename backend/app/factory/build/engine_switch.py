"""The database engine switch every platform ships: ``app/db.py``.

``DATABASE_URL`` was read in the generated kernel's config and used nowhere:
``grep -rln database_url`` over a live export returned that one file, while
``app/store.py`` was ``import sqlite3`` against a hardcoded
``<STORAGE_PATH>/platform.db``. An operator setting ``DATABASE_URL`` got a
platform that accepted the variable without complaint and quietly wrote a
SQLite file onto the container disk -- which is worse than not supporting
Postgres, because it looks supported.

The Factory cannot fix that by writing ``store.py``: the coder authors that
file, and overwriting it would destroy the capability work. So the Factory
owns the SWITCH and the floor obliges the author to route through it.
``app/db.py`` is substrate, present in every build; ``postgres_boot_200``
requires ``store.py`` to obtain its connection here and refuses a
``DATABASE_URL`` that is read and ignored.

Nothing here is domain-specific: it opens a database, whatever the platform
is for.
"""

from __future__ import annotations

_DB = '''"""Where this platform's database connection comes from.

Written by the factory. DATABASE_URL set means Postgres; absent means a
SQLite file under STORAGE_PATH for development. One place decides, so the
two cannot disagree.

app/store.py MUST obtain its connection from here and MUST NOT open its own
database. A DATABASE_URL that is read and then ignored is the failure this
module exists to prevent: the operator believes they are on Postgres while
the platform writes a SQLite file onto the container disk.

    from app.db import connect, engine, is_postgres

    with connect() as conn:
        ...
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any, Optional

#: Matched to the FastAPI sync threadpool: one writer, readers proceed.
SQLITE_BUSY_TIMEOUT_MS = 30000
SQLITE_CONNECT_TIMEOUT_S = 30.0
#: The statement itself is a constant: nothing reaches execute() as a
#: dynamically built string (the acceptance floor's audit_clean measures it).
_BUSY_TIMEOUT_PRAGMA = "PRAGMA busy_timeout=30000"

_ENGINE: Optional[Any] = None


def database_url() -> str:
    """The operator's choice, or empty for the SQLite development default.

    Hosts hand out ``postgres://`` or ``postgresql://``. SQLAlchemy reads a
    bare scheme as the psycopg2 driver, which this platform does not ship;
    it declares psycopg (v3), so the scheme is pinned to that driver here.
    """
    url = (os.environ.get("DATABASE_URL") or "").strip()
    for bare in ("postgres://", "postgresql://"):
        if url.startswith(bare):
            return "postgresql+psycopg://" + url[len(bare):]
    return url


def is_postgres() -> bool:
    return bool(database_url())


def sqlite_path() -> Path:
    root = Path(os.environ.get("STORAGE_PATH", "./data"))
    root.mkdir(parents=True, exist_ok=True)
    return root / "platform.db"


def engine() -> Any:
    """The SQLAlchemy engine for DATABASE_URL. Raises when there is none.

    Refuses at boot rather than degrading: a platform that cannot honour the
    variable must say so with the variable's name in the message, not fall
    back to SQLite and let the operator believe otherwise.
    """
    global _ENGINE
    url = database_url()
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set; this platform is on SQLite at "
            f"{sqlite_path()}. Use connect() for a connection that works on "
            "either backend."
        )
    if _ENGINE is None:
        try:
            from sqlalchemy import create_engine
        except ImportError as exc:  # pragma: no cover - deploy-time failure
            raise RuntimeError(
                "DATABASE_URL is set but SQLAlchemy is not installed, so this "
                "platform cannot honour it. Install sqlalchemy and a driver "
                "(psycopg[binary]) or unset DATABASE_URL."
            ) from exc
        _ENGINE = create_engine(url, pool_pre_ping=True, future=True)
    return _ENGINE


def connect() -> Any:
    """A live connection on whichever backend is configured.

    Postgres: a SQLAlchemy connection. SQLite: a stdlib sqlite3 connection
    with a busy timeout. Both are context managers and both are closed by
    the caller.

    journal_mode is NOT switched here. WAL is a persistent property of the
    file, set once by app.migrations.upgrade_head() at boot; switching it on
    every connection makes concurrent first-opens race for the write lock
    and raise 'database is locked' under the FastAPI threadpool.
    """
    if is_postgres():
        return engine().connect()
    conn = sqlite3.connect(
        str(sqlite_path()),
        timeout=SQLITE_CONNECT_TIMEOUT_S,
        check_same_thread=False,
        isolation_level="DEFERRED",
    )
    conn.row_factory = sqlite3.Row
    conn.execute(_BUSY_TIMEOUT_PRAGMA)
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def backend_name() -> str:
    """What /health and the acceptance harness report."""
    return "postgres" if is_postgres() else "sqlite"


def resolved_url() -> str:
    """The SQLAlchemy URL for whichever engine this platform is on.

    The one answer, for everything that needs a URL rather than a
    connection: alembic's env.py and app/migrations.py both read THIS. Each
    used to build its own -- env.py hardcoded sqlite and ignored
    DATABASE_URL, migrations assumed sqlite semantics on whatever came back
    -- so a platform with DATABASE_URL set migrated one database and served
    another. One place decides, so they cannot disagree.
    """
    url = database_url()
    if url:
        return url
    path = sqlite_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return "sqlite:///" + path.resolve().as_posix()
'''


def render_db_module() -> str:
    return _DB
