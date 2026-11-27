"""The default model id is what an unconfigured model slot resolves to.

The live Factory names its model in two settings; the code default must name the
same id, so a deployment that sets a key but no model asks the host for the model
the operator runs. An explicit model setting still wins over the default.
"""
from __future__ import annotations

import pytest

from app.core import llm_config

_ENV = (
    "LLM_PROVIDER",
    "CEREBRUM_LLM_API_KEY", "CEREBRUM_LLM_BASE_URL", "CEREBRUM_LLM_MODEL",
    "CEREBRUM_LLM_FALLBACK_MODEL",
    "CEREBRUM_CHAT_LLM_API_KEY", "CEREBRUM_CHAT_LLM_BASE_URL",
    "CEREBRUM_CHAT_LLM_MODEL", "CEREBRUM_CHAT_LLM_FALLBACK_MODEL",
    "CEREBRUM_FACTORY_LLM_API_KEY", "CEREBRUM_FACTORY_LLM_BASE_URL",
    "CEREBRUM_FACTORY_LLM_MODEL", "CEREBRUM_FACTORY_LLM_FALLBACK_MODEL",
    "OPENROUTER_API_KEY", "DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY",
    "CURSOR_API_KEY", "FACTORY_LLM_FALLBACK_API_KEY",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize(
    "env",
    [
        {"CEREBRUM_CHAT_LLM_API_KEY": "sk-scoped-a"},
        {"LLM_PROVIDER": "deepseek", "DEEPSEEK_API_KEY": "sk-global-b"},
    ],
)
def test_a_key_with_no_model_resolves_to_the_default_model_id(monkeypatch, env):
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    cfg = llm_config.get_llm_config()
    assert cfg["model"] == "deepseek-flash"
    assert cfg["fallback_model"] == "deepseek-flash"
    assert cfg["base_url"] == llm_config.DEEPSEEK_BASE_URL


@pytest.mark.parametrize("named", ["deepseek-chat", "deepseek-reasoner"])
def test_an_explicit_model_setting_still_wins_over_the_default(monkeypatch, named):
    monkeypatch.setenv("CEREBRUM_CHAT_LLM_API_KEY", "sk-scoped")
    monkeypatch.setenv("CEREBRUM_CHAT_LLM_MODEL", named)
    cfg = llm_config.get_llm_config()
    assert cfg["model"] == named
    assert cfg["fallback_model"] == "deepseek-flash"


@pytest.mark.parametrize("named", ["deepseek-chat", "deepseek-reasoner"])
def test_an_explicit_fallback_setting_still_wins_over_the_default(monkeypatch, named):
    monkeypatch.setenv("CEREBRUM_CHAT_LLM_API_KEY", "sk-scoped")
    monkeypatch.setenv("CEREBRUM_CHAT_LLM_FALLBACK_MODEL", named)
    cfg = llm_config.get_llm_config()
    assert cfg["model"] == "deepseek-flash"
    assert cfg["fallback_model"] == named
