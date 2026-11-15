"""WK-2: one provider table shared by the config and the coder.

The fault: ``core/llm_config`` resolved a provider name that
``factory/coder`` then refused with "unsupported coder provider", so every
HTTP-coder call raised and the tester's cases and the collector review were
skipped as notes. The fix is one table both sides read. These tests run
config -> coder call for EVERY provider in that table, so a provider added
to the config without the coder being able to call it fails here.

No network: the coder's POST seam is replaced by a recorder. Keys are
generated per run; no value is stored in the repository.
"""

from __future__ import annotations

import os
import re
import secrets

import pytest

from app.core.llm_config import (
    HTTP_CALL_PROVIDERS,
    SUPPORTED_PROVIDERS,
    get_factory_llm_config,
)
from app.factory import coder

#: Every setting that can steer provider selection is cleared first, so the
#: test sees only what it sets (CI and a developer shell differ otherwise).
_STEERING = re.compile(
    r"^(LLM_|KIMI_|MOONSHOT_|ANTHROPIC_|CLAUDE_|CURSOR_|DEEPSEEK_|OPENROUTER_|"
    r"FACTORY_LLM_|FACTORY_CURSOR_|FACTORY_CODE_|CEREBRUM_)"
)

_ANSWER = "print('written by the provider under test')"


def _clean_env(monkeypatch) -> None:
    for name in list(os.environ):
        if _STEERING.match(name):
            monkeypatch.delenv(name, raising=False)
    # No cross-provider leg: the primary alone must be callable.
    monkeypatch.setenv("FACTORY_LLM_FALLBACK_PROVIDER", "none")


def _arm_every_key(monkeypatch) -> None:
    """A generated key in every slot a provider reads, so the provider named
    by LLM_PROVIDER is the only thing that differs between cases."""
    for slot in (
        "DEEPSEEK_API_KEY",
        "CEREBRUM_LLM_API_KEY",
        "CEREBRUM_CHAT_LLM_API_KEY",
        "ANTHROPIC_API_KEY",
    ):
        monkeypatch.setenv(slot, "sk-" + secrets.token_hex(12))


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return _Resp()


class _Resp:
    status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        # Both wire shapes: OpenAI-compatible and Anthropic Messages.
        return {
            "choices": [{"message": {"content": _ANSWER}, "finish_reason": "stop"}],
            "content": [{"type": "text", "text": _ANSWER}],
        }


def test_config_and_coder_read_one_table():
    assert HTTP_CALL_PROVIDERS is SUPPORTED_PROVIDERS


@pytest.mark.parametrize("provider", list(HTTP_CALL_PROVIDERS))
def test_every_config_provider_reaches_the_coder(monkeypatch, provider):
    _clean_env(monkeypatch)
    _arm_every_key(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", provider)

    cfg = get_factory_llm_config()
    assert cfg.get("error") is None, cfg.get("error")
    assert cfg["provider"] == provider

    rec = _Recorder()
    monkeypatch.setattr(coder, "post_with_deadline", rec)
    # The impl directly, with no attempt deadline set by the wrapper: the
    # impl must set its own deadline rather than run unbounded.
    text, model = coder._llm_code_call_impl([{"role": "user", "content": "u"}])

    assert text == _ANSWER
    assert model == cfg["model"]
    assert len(rec.calls) == 1
    assert rec.calls[0]["url"].startswith(cfg["base_url"].rstrip("/"))
    assert isinstance(rec.calls[0]["deadline_mono"], float)


@pytest.mark.parametrize(
    ("provider", "mock_flag"),
    [("claude", "CLAUDE_MOCK"), ("cursor", "CEREBRUM_LLM_MOCK")],
)
def test_mock_config_is_refused_before_any_call(monkeypatch, provider, mock_flag):
    _clean_env(monkeypatch)
    _arm_every_key(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", provider)
    monkeypatch.setenv(mock_flag, "1")
    rec = _Recorder()
    monkeypatch.setattr(coder, "post_with_deadline", rec)
    assert get_factory_llm_config()["mock"] is True

    with pytest.raises(coder.CoderError, match="mock"):
        coder._llm_code_call_impl([{"role": "user", "content": "u"}])
    assert rec.calls == []


@pytest.mark.parametrize("key_slot", ["CEREBRUM_LLM_API_KEY", "CEREBRUM_CHAT_LLM_API_KEY"])
def test_unnamed_provider_does_not_default_to_the_retired_host(monkeypatch, key_slot):
    _clean_env(monkeypatch)
    monkeypatch.setenv(key_slot, "sk-" + secrets.token_hex(12))

    cfg = get_factory_llm_config()

    assert cfg["provider"] in HTTP_CALL_PROVIDERS
    assert "moonshot" not in cfg["base_url"].lower()
    assert "kimi" not in cfg["model"].lower()
