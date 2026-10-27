"""The writer is the same LLM as Floor chat, so it shares the platform's
authoritative key. A stale ``DEEPSEEK_API_KEY`` left in the deploy env (a
rotated key) must not shadow the working ``CEREBRUM_CHAT_LLM_API_KEY`` -- that
shadowing stalled a live build at WRITER on an invalid-key auth error. The
writer is the same LLM, so it uses the same key.
"""
from __future__ import annotations

from app.factory.build import codewhale_worker

_KEYS = ("CODEWHALE_API_KEY", "CEREBRUM_CHAT_LLM_API_KEY", "DEEPSEEK_API_KEY")


def _clear(monkeypatch) -> None:
    for key in _KEYS:
        monkeypatch.delenv(key, raising=False)


def test_explicit_codewhale_key_overrides_everything(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("CODEWHALE_API_KEY", "sk-codewhale")
    monkeypatch.setenv("CEREBRUM_CHAT_LLM_API_KEY", "sk-chat")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-stale")
    assert codewhale_worker.worker_api_key() == "sk-codewhale"


def test_platform_chat_key_beats_a_stale_deepseek_export(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("CEREBRUM_CHAT_LLM_API_KEY", "sk-chat")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-stale")
    assert codewhale_worker.worker_api_key() == "sk-chat"


def test_only_the_platform_key_is_used_when_set_alone(monkeypatch):
    # Second generic red-first input: with ONLY the platform key set, the old
    # code (CODEWHALE_API_KEY or DEEPSEEK_API_KEY) returned "" -- the writer had
    # no key though a valid one was present. It must now resolve to it.
    _clear(monkeypatch)
    monkeypatch.setenv("CEREBRUM_CHAT_LLM_API_KEY", "sk-chat")
    assert codewhale_worker.worker_api_key() == "sk-chat"


def test_legacy_deepseek_key_is_the_last_resort(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-legacy")
    assert codewhale_worker.worker_api_key() == "sk-legacy"


def test_no_key_is_empty_so_the_cli_uses_its_own_config(monkeypatch):
    _clear(monkeypatch)
    assert codewhale_worker.worker_api_key() == ""
