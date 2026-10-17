"""Factory health/ready endpoint tests."""

from __future__ import annotations


def test_health_reports_storage(client, tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    res = client.get("/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] in {"ok", "degraded"}
    assert "storage" in body


def test_health_frontend_url_public_is_bool_never_echoes_url(client, monkeypatch):
    monkeypatch.setenv("FRONTEND_URL", "https://www.cerebrum-dev.com")
    body = client.get("/health").json()
    assert body["frontend_url_public"] is True
    dumped = str(body)
    assert "www.cerebrum-dev.com" not in dumped
    assert "FRONTEND_URL" not in dumped

    monkeypatch.setenv("FRONTEND_URL", "http://localhost:5173")
    assert client.get("/health").json()["frontend_url_public"] is False

    monkeypatch.delenv("FRONTEND_URL", raising=False)
    assert client.get("/health").json()["frontend_url_public"] is False


def test_ready_endpoint(client, tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    monkeypatch.setenv("ENV", "test")
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)
    res = client.get("/ready")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] in {"ready", "not_ready"}
    assert "checks" in body
    backup = body["details"]["last_backup"]
    assert backup["ok"] in {True, False}
    if backup["ok"] is False:
        assert backup.get("error")
    llm = body["details"]["llm"]
    for name in (
        "cerebrum_chat_llm_api_key_present",
        "cerebrum_llm_api_key_present",
        "kimi_api_key_present",
        "openrouter_api_key_present",
        "cursor_api_key_present",
        "chat_http_api_key_present",
    ):
        assert isinstance(llm[name], bool), name
    assert isinstance(llm["llm_provider"], str)
    assert isinstance(llm["chat_http_base_url_host"], str)
    assert isinstance(llm["chat_http_error"], str)


def test_version_endpoint_does_not_invent_a_sha(client, monkeypatch):
    monkeypatch.delenv("RENDER_GIT_COMMIT", raising=False)
    monkeypatch.delenv("GIT_COMMIT", raising=False)
    monkeypatch.delenv("SOURCE_VERSION", raising=False)
    res = client.get("/version")
    assert res.status_code == 200
    body = res.json()
    assert body["service"] == "cerebrumdev-factory"
    assert body["git_sha"] is None
    assert body["git_sha_short"] is None
    assert "sentry_configured" in body


def test_version_endpoint_reports_render_sha(client, monkeypatch):
    monkeypatch.setenv("RENDER_GIT_COMMIT", "abcdef1234567890")
    res = client.get("/version")
    assert res.status_code == 200
    body = res.json()
    assert body["git_sha"] == "abcdef1234567890"
    assert body["git_sha_short"] == "abcdef1"


def test_ready_backup_reports_live_host_mismatch(client, tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    monkeypatch.setenv("ENV", "test")
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)
    monkeypatch.setenv(
        "ACCOUNTS_DATABASE_URL",
        "postgresql://u:p@ep-new.aws.neon.tech/accounts",
    )
    from app.core import backup_scheduler as sched

    sched.status_path().parent.mkdir(parents=True, exist_ok=True)
    sched.status_path().write_text(
        '{"ok": true, "at": "2026-08-16T23:49:44+00:00", "engine": "postgres",'
        ' "accounts_host": "dpg-old.oregon-postgres.render.com"}',
        encoding="utf-8",
    )
    res = client.get("/ready")
    assert res.status_code == 200
    backup = res.json()["details"]["last_backup"]
    from app.core.backup import public_accounts_host_label

    assert backup["accounts_host"] == public_accounts_host_label(
        "dpg-old.oregon-postgres.render.com"
    )
    assert backup["live_accounts_host"] == public_accounts_host_label(
        "ep-new.aws.neon.tech"
    )
    assert "ep-new.aws.neon.tech" not in str(backup)
    assert backup["matches_live_engine"] is False


def test_ready_fails_in_production_when_drive_lacks_encryption(
    client, tmp_path, monkeypatch
):
    """M5: production + Drive credentials + no DATA_ENCRYPTION_KEY → not ready."""
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("CEREBRUM_DEV_API_KEY", "master-for-ready")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "drive-client")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "drive-secret")
    monkeypatch.delenv("DATA_ENCRYPTION_KEY", raising=False)
    res = client.get("/ready")
    assert res.status_code == 503
    body = res.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["data_encryption"] is False
    assert body["details"]["data_encryption"]["drive_configured"] is True
    assert body["details"]["data_encryption"]["encryption_enabled"] is False


def test_ready_head_matches_get_status(client, tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    monkeypatch.setenv("ENV", "test")
    get_res = client.get("/ready")
    head_res = client.head("/ready")
    assert head_res.status_code == get_res.status_code


def test_ops_backup_requires_master_key(client, monkeypatch):
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)
    res = client.post("/v1/ops/backup")
    assert res.status_code == 404


def test_ops_backup_rejects_wrong_key(client, monkeypatch):
    monkeypatch.setenv("CEREBRUM_DEV_API_KEY", "master-secret")
    res = client.post("/v1/ops/backup", headers={"Authorization": "Bearer nope"})
    assert res.status_code == 401


def test_ops_backup_runs_and_overwrites_stale_fail(client, tmp_path, monkeypatch):
    monkeypatch.setenv("CEREBRUM_DEV_API_KEY", "master-secret")
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.delenv("BACKUP_DIR", raising=False)
    monkeypatch.delenv("ACCOUNTS_DATABASE_URL", raising=False)
    monkeypatch.delenv("ACCOUNTS_DB_PATH", raising=False)
    from app.core import backup as bk
    from app.core import backup_scheduler as sched

    root = bk.backup_root()
    root.mkdir(parents=True, exist_ok=True)
    sched.status_path().write_text(
        '{"ok": false, "at": "2026-08-21T03:00:00+00:00",'
        ' "error": "accounts dump failed: pg_dump failed with exit code 1"}',
        encoding="utf-8",
    )
    db = tmp_path / "storage" / "accounts.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    import sqlite3

    conn = sqlite3.connect(str(db))
    try:
        conn.execute("CREATE TABLE accounts (id TEXT PRIMARY KEY, email TEXT)")
        conn.execute("INSERT INTO accounts VALUES ('a1', 'ops@example.com')")
        conn.commit()
    finally:
        conn.close()

    res = client.post(
        "/v1/ops/backup", headers={"Authorization": "Bearer master-secret"}
    )
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert sched.last_status()["ok"] is True
    ready = client.get("/ready").json()["details"]["last_backup"]
    assert ready["ok"] is True
    # Public /ready hashes the host; the master-key backup report keeps it.
    if body.get("accounts_host"):
        assert "neon.tech" in str(body.get("accounts_host")) or "." in str(
            body.get("accounts_host")
        )
        assert ready.get("accounts_host") != body.get("accounts_host") or ready.get(
            "accounts_host", ""
        ).startswith("sha256:")
