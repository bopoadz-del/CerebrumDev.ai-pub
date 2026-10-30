"""Follow-up to #108 (item 13): pin the admin verify-email edges G6's sample
found unpinned. Synthetic accounts only, in pytest's tmp_path. The route is
reached through app.main.app; the store through app.core.accounts_store.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

MASTER = "synthetic-master-key-p13c"
ADMIN = {"X-API-Key": MASTER}
PASSWORD = "synthetic-pass-456"
URL = "/v1/auth/admin/verify-email"


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.setenv("ACCOUNTS_DB_PATH", str(tmp_path / "accounts.db"))
    monkeypatch.delenv("ACCOUNTS_DATABASE_URL", raising=False)
    monkeypatch.setenv("CEREBRUM_DEV_API_KEY", MASTER)
    monkeypatch.setenv("ACCOUNTS_REQUIRE_VERIFIED_EMAIL", "1")
    monkeypatch.delenv("ALLOW_ANONYMOUS_DEV", raising=False)
    monkeypatch.delenv("ACCOUNTS_EXPOSE_DEV_TOKENS", raising=False)
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("AUTH_RATE_LIMIT_MAX", raising=False)
    import app.core.rate_limit

    app.core.rate_limit.reset_rate_limits()
    import app.main

    return TestClient(app.main.app)


def _register(client, email):
    reg = client.post("/v1/auth/register", json={"email": email, "password": PASSWORD})
    assert reg.status_code == 201, reg.text
    return reg.json()["account_id"]


def _row(account_id):
    import app.core.accounts_store

    store = app.core.accounts_store
    cols = store._t_accounts.c
    with store._engine().begin() as conn:
        row = conn.execute(
            store.sa.select(cols.email_verified, cols.verify_token_hash, cols.verify_expires_at)
            .where(cols.id == account_id)
        ).one()
    return bool(row[0]), row[1], row[2]


@pytest.mark.parametrize("pad", ["   {}   ", "\n{}\t"])
def test_padded_id_resolves_to_the_account(client, pad):
    acct = _register(client, "pad-p13c@example.test")
    res = client.post(URL, headers=ADMIN, json={"account_id": pad.format(acct)})
    assert res.status_code == 200, res.text
    assert _row(acct)[0] is True


@pytest.mark.parametrize("blank", ["   ", "\t\n", ""])
def test_blank_or_whitespace_id_is_400_with_exact_body(client, blank):
    res = client.post(URL, headers=ADMIN, json={"account_id": blank})
    assert res.status_code == 400
    assert res.json() == {"detail": "account_id required"}


@pytest.mark.parametrize("missing", ["acct_p13c_missing", "acct_nobody_0000"])
def test_unknown_id_is_404_with_exact_body(client, missing):
    _register(client, "bystander-p13c@example.test")
    res = client.post(URL, headers=ADMIN, json={"account_id": missing})
    assert res.status_code == 404
    assert res.json() == {"detail": "Account not found"}


@pytest.mark.parametrize("email", ["clear-a-p13c@example.test", "clear-b-p13c@example.test"])
def test_store_clears_token_and_expiry_on_verify(client, email):
    import app.core.accounts_store

    acct = _register(client, email)
    app.core.accounts_store.issue_verify_token(acct)
    before = _row(acct)
    assert before[1] is not None and before[2] is not None

    assert app.core.accounts_store.mark_account_verified(acct) is True

    assert _row(acct) == (True, None, None)


@pytest.mark.parametrize("target_first", [True, False])
def test_store_where_touches_only_the_target(client, target_first):
    import app.core.accounts_store

    a = _register(client, "target-p13c@example.test")
    b = _register(client, "other-p13c@example.test")
    app.core.accounts_store.issue_verify_token(a)
    app.core.accounts_store.issue_verify_token(b)
    target, other = (a, b) if target_first else (b, a)
    other_before = _row(other)
    assert other_before[0] is False and other_before[1] is not None

    assert app.core.accounts_store.mark_account_verified(target) is True

    assert _row(target) == (True, None, None)
    assert _row(other) == other_before
