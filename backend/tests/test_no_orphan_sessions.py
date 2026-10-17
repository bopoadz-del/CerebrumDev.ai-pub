"""A session must never exist without a recorded owner.

The ownership row is the only link between a session and an account. Without
it the session is invisible to every per-account query -- it cannot be listed,
exported, or erased on request -- while still holding chat history and uploaded
documents. That is an unerasable personal-data record, created silently.

The original code recorded ownership after creating the session and swallowed
any failure with a warning. The shape that catches that is not "does create
work" (it always did) but "when the ownership write fails, does a session exist
anyway".
"""

from __future__ import annotations

import os

os.environ.setdefault("ALLOW_ANONYMOUS_DEV", "1")

import pytest  # noqa: E402

from app.core import session_store  # noqa: E402


@pytest.fixture(autouse=True)
def clean_store(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    session_store._session_store.clear()
    yield
    session_store._session_store.clear()


def test_ownership_failure_creates_no_session(monkeypatch):
    """The load-bearing case: a failed ownership write must abort creation."""

    def boom(session_id, account_id):
        raise RuntimeError("accounts database unavailable")

    monkeypatch.setattr("app.core.accounts_store.record_session_owner", boom)

    with pytest.raises(RuntimeError):
        session_store.create_session("sess_orphan_1", "acc_1")

    assert "sess_orphan_1" not in session_store._session_store, (
        "an unowned session was created; it can never be listed or erased"
    )
    assert session_store.get_session("sess_orphan_1") is None


def test_ownership_is_recorded_before_content_exists(monkeypatch):
    """Order matters: ownership first, so there is no window of orphanhood."""
    events = []

    def record(session_id, account_id):
        events.append(("owner", session_id))

    monkeypatch.setattr("app.core.accounts_store.record_session_owner", record)
    monkeypatch.setattr(
        session_store,
        "save_session_state",
        lambda state: events.append(("saved", state.session_id)),
    )

    session_store.create_session("sess_order", "acc_1")

    assert events[0][0] == "owner", f"session persisted before ownership: {events}"


def test_happy_path_still_creates_and_owns(monkeypatch):
    recorded = []
    monkeypatch.setattr(
        "app.core.accounts_store.record_session_owner",
        lambda s, a: recorded.append((s, a)),
    )

    state = session_store.create_session("sess_ok", "acc_9")

    assert state.session_id == "sess_ok"
    assert recorded == [("sess_ok", "acc_9")]


def test_anonymous_sessions_do_not_require_an_owner_row(monkeypatch):
    """Local/dev anonymous use has no account to own the session."""

    def boom(session_id, account_id):  # must not be reached
        raise AssertionError("should not record an owner for anonymous")

    monkeypatch.setattr("app.core.accounts_store.record_session_owner", boom)

    state = session_store.create_session("sess_anon", "anonymous")
    assert state.user_id == "anonymous"
