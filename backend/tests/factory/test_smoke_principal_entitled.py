"""The smoke principal is the paid-customer stand-in: it must be entitled.

ensure_verified_account (the production smoke gate's account) is created with an
ACTIVE subscription, not a lapse-able trial, so the deploy pilot's Journey B
(document upload) never turns red because the smoke account's trial expired.
Public register (create_account) is unchanged and still starts a trial.
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def store(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.delenv("ACCOUNTS_DB_PATH", raising=False)
    monkeypatch.delenv("ACCOUNTS_DATABASE_URL", raising=False)
    monkeypatch.delenv("TRIAL_DAYS", raising=False)

    from app.core import accounts_store

    return accounts_store


@pytest.mark.parametrize(
    "creator,expect_status",
    [
        ("smoke", "active"),
        ("public", "trialing"),
    ],
)
def test_smoke_account_is_active_public_account_trials(store, creator, expect_status):
    """Two creation paths -> two subscription states. ensure_verified_account
    (smoke) is active/entitled; create_account (public register) is trialing."""
    if creator == "smoke":
        account = store.ensure_verified_account("smoke-a@example.com", "pilot-pass-123")
    else:
        account = store.create_account("user-a@example.com", "pilot-pass-123")
    fields = store.subscription_fields(account["account_id"])
    assert fields["subscription_status"] == expect_status


def test_re_ensuring_the_smoke_account_resets_its_password(store):
    """The ops-only account is update-in-place: re-ensuring it resets its
    password (the else branch). Two inputs (two passwords)."""
    first = store.ensure_verified_account("smoke-b@example.com", "pass-one-123")
    second = store.ensure_verified_account("smoke-b@example.com", "pass-two-123")
    assert second["account_id"] == first["account_id"]
    assert store.verify_account_password(first["account_id"], "pass-two-123") is True
    assert store.verify_account_password(first["account_id"], "pass-one-123") is False


def test_ensure_verified_account_raises_if_the_account_is_lost(store, monkeypatch):
    """The defensive guard: if the freshly-written account cannot be re-read, the
    function raises rather than returning a half-written principal."""
    monkeypatch.setattr(store, "get_account", lambda account_id: None)
    with pytest.raises(RuntimeError, match="lost the account"):
        store.ensure_verified_account("smoke-c@example.com", "pass-three-123")
