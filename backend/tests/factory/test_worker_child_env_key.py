"""The writer child env must carry the RESOLVED key on the vars the CLI reads,
so a stale provider key left in os.environ cannot shadow it. A live build
stalled at WRITER on exactly that: the codewhale CLI read a rotated provider
key from its inherited environment and preferred it over everything else. The
key is env-only -- it is never passed on argv.
"""
from __future__ import annotations

from app.factory.build import codewhale_worker

_KEYS = ("CODEWHALE_API_KEY", "CEREBRUM_CHAT_LLM_API_KEY", "DEEPSEEK_API_KEY", "CODEWHALE_PROVIDER")


def test_child_env_overrides_a_stale_provider_key(monkeypatch):
    for key in _KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CEREBRUM_CHAT_LLM_API_KEY", "sk-valid")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-stale")  # rotated; must not win
    env = codewhale_worker._child_env(session_id="sess_x")
    # Default provider is deepseek, so the CLI reads DEEPSEEK_API_KEY; both it
    # and the CLI's own CODEWHALE_API_KEY must carry the resolved valid key.
    assert env["DEEPSEEK_API_KEY"] == "sk-valid"
    assert env["CODEWHALE_API_KEY"] == "sk-valid"


def test_child_env_stamps_the_configured_provider_var(monkeypatch):
    # Second generic red-first input: a NON-default provider. The stamped var is
    # derived from worker_provider() (<PROVIDER>_API_KEY), not hard-coded to
    # deepseek -- CODEWHALE_PROVIDER drives it. Red on pre-fix code (no stamp).
    for key in (*_KEYS, "ACME_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CODEWHALE_PROVIDER", "acme")
    monkeypatch.setenv("ACME_API_KEY", "sk-stale")  # a stale provider key
    monkeypatch.setenv("CEREBRUM_CHAT_LLM_API_KEY", "sk-valid")
    env = codewhale_worker._child_env(session_id="sess_x")
    assert env["ACME_API_KEY"] == "sk-valid"
    assert env["CODEWHALE_API_KEY"] == "sk-valid"
    assert "DEEPSEEK_API_KEY" not in env  # deepseek is not the provider here


def test_child_env_without_any_key_stamps_nothing(monkeypatch):
    for key in _KEYS:
        monkeypatch.delenv(key, raising=False)
    env = codewhale_worker._child_env(session_id="sess_x")
    # No key anywhere -> nothing stamped; the CLI falls back to its own config.
    assert "DEEPSEEK_API_KEY" not in env
    assert "CODEWHALE_API_KEY" not in env
