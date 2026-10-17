"""Non-root Docker + libpq default client cert must not kill Neon boot.

Measured production failure after 7ea3c48: setpriv drops to uid 10001 while
HOME stays /root. libpq then fails opening
``/root/.postgresql/postgresql.crt`` (EACCES) during ``alembic upgrade head``.
Neon does not issue a client cert; ``sslmode=require`` is enough.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from app.core.accounts_store import (
    normalize_accounts_database_url,
    prepare_libpq_client_env,
)
from app.core.backup import _sqlalchemy_accounts_url

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def test_prepare_remaps_unreadable_home(tmp_path, monkeypatch):
    missing = tmp_path / "does-not-exist"
    fallback = tmp_path / "writable"
    fallback.mkdir()
    monkeypatch.setenv("HOME", str(missing))
    monkeypatch.setenv("STORAGE_PATH", str(fallback))
    monkeypatch.delenv("PGSSLCERT", raising=False)
    monkeypatch.delenv("PGSSLKEY", raising=False)

    prepare_libpq_client_env()

    assert os.environ["HOME"] == str(fallback)
    assert os.access(os.environ["HOME"], os.R_OK | os.X_OK)


def test_prepare_unsets_unreadable_and_root_client_cert(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    missing = tmp_path / "no-such" / "postgresql.crt"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PGSSLCERT", "/root/.postgresql/postgresql.crt")
    monkeypatch.setenv("PGSSLKEY", str(missing))

    prepare_libpq_client_env()

    assert "PGSSLCERT" not in os.environ
    assert "PGSSLKEY" not in os.environ
    assert os.environ["HOME"] == str(home)


def test_prepare_keeps_readable_client_cert(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    cert = tmp_path / "client.crt"
    cert.write_text("not-a-real-cert", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PGSSLCERT", str(cert))

    prepare_libpq_client_env()

    assert os.environ["PGSSLCERT"] == str(cert)


def test_normalize_strips_unreadable_sslcert_keeps_sslmode():
    url = (
        "postgresql://u:p@ep-sweet-hill-ay5e13we.c-5.us-east-2.aws.neon.tech/db"
        "?sslmode=require&sslcert=/root/.postgresql/postgresql.crt"
        "&sslkey=/root/.postgresql/postgresql.key"
    )
    out = normalize_accounts_database_url(url)
    parsed = urlparse(out)
    query = parse_qs(parsed.query)
    assert parsed.scheme == "postgresql+psycopg"
    assert query.get("sslmode") == ["require"]
    assert "sslcert" not in query
    assert "sslkey" not in query


def test_normalize_never_infers_sslmode_from_the_hosts_spelling():
    """Was test_normalize_adds_sslmode_require_for_neon_without_inventing_cert.

    The TLS mode used to be added when the host ended in one vendor's domain
    -- behaviour decided by a literal name. The mode is now the operator's to
    declare in the URL; an undeclared mode is left to libpq, and a declared
    one is kept (test_normalize_strips_unreadable_sslcert_keeps_sslmode).
    """
    url = "postgresql://u:p@ep-example.c-5.us-east-2.aws.neon.tech/db"
    out = normalize_accounts_database_url(url)
    parsed = urlparse(out)
    query = parse_qs(parsed.query)
    assert parsed.scheme == "postgresql+psycopg"
    assert "sslmode" not in query
    assert "sslcert" not in query
    assert "sslkey" not in query


def test_normalize_keeps_an_explicit_driver():
    out = normalize_accounts_database_url("postgresql+asyncpg://u:p@db:5432/x")
    assert urlparse(out).scheme == "postgresql+asyncpg"
    assert normalize_accounts_database_url("sqlite:///a.db") == "sqlite:///a.db"


def test_normalize_does_not_force_sslmode_on_generic_postgres():
    out = normalize_accounts_database_url("postgresql://u:p@localhost:5432/db")
    assert out == "postgresql+psycopg://u:p@localhost:5432/db"


def test_accounts_store_and_backup_share_the_same_normalizer(monkeypatch):
    raw = (
        "postgres://u:p@ep-x.aws.neon.tech/db"
        "?sslmode=require&sslcert=/root/.postgresql/postgresql.crt"
    )
    monkeypatch.setenv("ACCOUNTS_DATABASE_URL", raw)
    from app.core import accounts_store

    store_url = accounts_store._database_url()
    backup_url = _sqlalchemy_accounts_url(raw)
    assert store_url == backup_url
    assert "sslcert" not in store_url
    assert "sslmode=require" in store_url


def test_alembic_boot_uses_shared_libpq_hygiene():
    env_py = (BACKEND_ROOT / "alembic" / "env.py").read_text(encoding="utf-8")
    assert "prepare_libpq_client_env" in env_py
    assert "from app.core.accounts_store import" in env_py


def test_alembic_upgrade_with_sealed_home_does_not_mention_root_cert(tmp_path):
    """Boot-shaped alembic invoke: unreadable HOME + PGSSLCERT under /root.

    Port 9 cannot complete TLS, so this asserts the process does not die
    on the measured client-cert path. Connection failure is expected.
    """
    sealed = tmp_path / "sealed_home"
    sealed.mkdir()
    sealed.chmod(0)
    env = os.environ.copy()
    for stale in ("ACCOUNTS_DB_PATH", "STORAGE_PATH"):
        env.pop(stale, None)
    env["HOME"] = str(sealed)
    env["PGSSLCERT"] = "/root/.postgresql/postgresql.crt"
    env["PGSSLKEY"] = "/root/.postgresql/postgresql.key"
    env["ACCOUNTS_DATABASE_URL"] = "postgresql://u:p@127.0.0.1:9/none"
    env["PYTHONPATH"] = str(BACKEND_ROOT)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=BACKEND_ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
    finally:
        sealed.chmod(stat.S_IRWXU)

    combined = (result.stdout or "") + (result.stderr or "")
    assert result.returncode != 0
    assert "postgresql.crt" not in combined
    assert "Permission denied" not in combined
    assert "No module named 'psycopg2'" not in combined
