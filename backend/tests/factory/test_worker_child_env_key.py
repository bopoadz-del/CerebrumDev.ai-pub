"""The writer child env must carry the RESOLVED key on the vars the CLI reads,
so a stale DEEPSEEK_API_KEY left in os.environ cannot shadow it. The live Fleet
build stalled at WRITER on exactly that: the codewhale CLI read a rotated
DEEPSEEK_API_KEY from its inherited environment and preferred it over the valid
key passed on --api-key.
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


def test_child_env_without_any_key_stamps_nothing(monkeypatch):
    for key in _KEYS:
        monkeypatch.delenv(key, raising=False)
    env = codewhale_worker._child_env(session_id="sess_x")
    # No key anywhere -> nothing stamped; the CLI falls back to its own config.
    assert "DEEPSEEK_API_KEY" not in env
    assert "CODEWHALE_API_KEY" not in env
