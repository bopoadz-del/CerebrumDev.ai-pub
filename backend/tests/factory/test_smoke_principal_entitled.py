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
