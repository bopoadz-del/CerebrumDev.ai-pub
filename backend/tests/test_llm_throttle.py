"""Burst throttle on LLM-backed routes.

New-shape tests for the PRR fix: trial quotas exempt active subscribers, so a
subscribed (or leaked) key previously had NO limit of any kind on the LLM
routes — free to serialize the single-worker service under back-to-back 120 s
LLM calls. The throttle binds every principal.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.core import rate_limit
from app.core.llm_throttle import require_llm_rate


@pytest.fixture(autouse=True)
def _fresh_buckets():
    rate_limit.reset_rate_limits()
    yield
    rate_limit.reset_rate_limits()


def _principal(account="acct-1"):
    return SimpleNamespace(account_id=account, kind="user")


def test_throttle_trips_and_reports_retry_after(monkeypatch):
    monkeypatch.setenv("LLM_RATE_LIMIT_MAX", "3")
    monkeypatch.setenv("LLM_RATE_LIMIT_WINDOW_S", "60")
    p = _principal()
    for _ in range(3):
        require_llm_rate(p, "generate")
    with pytest.raises(HTTPException) as exc:
        require_llm_rate(p, "generate")
    assert exc.value.status_code == 429
    assert exc.value.detail["error"] == "rate_limited"
    assert exc.value.headers["Retry-After"] == "60"


def test_binds_subscribers_too(monkeypatch):
    """The whole point: unlike trial quotas, no subscription exemption exists.

    require_llm_rate never consults accounts_store/subscription state — it
    keys purely on the account id, so an active subscriber trips it the same.
    """
    src = inspect.getsource(require_llm_rate)
    assert "subscription" not in src and "accounts_store" not in src
    monkeypatch.setenv("LLM_RATE_LIMIT_MAX", "1")
    p = _principal("paying-customer")
    require_llm_rate(p, "chat")
    with pytest.raises(HTTPException):
        require_llm_rate(p, "chat")


def test_accounts_do_not_share_buckets(monkeypatch):
    monkeypatch.setenv("LLM_RATE_LIMIT_MAX", "1")
    require_llm_rate(_principal("a"), "draft")
    require_llm_rate(_principal("b"), "draft")  # must not raise


def test_buckets_are_per_route_kind(monkeypatch):
    monkeypatch.setenv("LLM_RATE_LIMIT_MAX", "1")
    p = _principal()
    require_llm_rate(p, "draft")
    require_llm_rate(p, "generate")  # separate bucket, must not raise


def test_bare_account_id_string_accepted(monkeypatch):
    """The chat route only has state.user_id, not a Principal."""
    monkeypatch.setenv("LLM_RATE_LIMIT_MAX", "1")
    require_llm_rate("user-42", "chat")
    with pytest.raises(HTTPException):
        require_llm_rate("user-42", "chat")


def test_zero_disables(monkeypatch):
    monkeypatch.setenv("LLM_RATE_LIMIT_MAX", "0")
    for _ in range(50):
        require_llm_rate(_principal(), "generate")


def test_every_llm_route_is_wired():
    """Pin the coverage: each LLM-backed route module calls require_llm_rate
    for each guarded action, so a new route can't silently ship unthrottled."""
    import app.routers.chat as chat
    import app.routers.session_product as sp

    assert 'require_llm_rate(getattr(state, "user_id", None), "chat")' in inspect.getsource(chat)
    sp_src = inspect.getsource(sp)
    for bucket in ("draft", "plan", "generate"):
        assert f'require_llm_rate(principal, "{bucket}")' in sp_src, bucket
