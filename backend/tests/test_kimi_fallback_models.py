"""Fallback-model regression tests for Kimi-only LLM paths.

If the primary Kimi model fails, each path retries once with a configured
fallback model before giving up.
"""

import os
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.core.chain_generator import _call_openai_compatible
from app.core.llm_config import get_factory_llm_config, get_llm_config
from app.factory.product_architect import _llm_json_call


@pytest.fixture(autouse=True)
def _clear_env():
    keys = [
        "LLM_PROVIDER",
        "CEREBRUM_LLM_API_KEY",
        "CEREBRUM_LLM_BASE_URL",
        "CEREBRUM_LLM_MODEL",
        "CEREBRUM_CHAT_LLM_MODEL",
        "CEREBRUM_CHAT_LLM_FALLBACK_MODEL",
        "CEREBRUM_FACTORY_LLM_MODEL",
        "CEREBRUM_FACTORY_LLM_FALLBACK_MODEL",
        "KIMI_API_KEY",
        "KIMI_MODEL",
        "KIMI_MOCK",
        "CEREBRUM_LLM_MOCK",
    ]
    old = {k: os.environ.get(k) for k in keys}
    for k in keys:
        os.environ.pop(k, None)
    yield
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


@pytest.fixture
def chat_env(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "kimi")
    monkeypatch.setenv("KIMI_API_KEY", "sk-test")
    monkeypatch.setenv("CEREBRUM_CHAT_LLM_MODEL", "kimi-k2.7")
    monkeypatch.setenv("CEREBRUM_CHAT_LLM_FALLBACK_MODEL", "moonshot-v1-8k")


@pytest.fixture
def factory_env(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "kimi")
    monkeypatch.setenv("KIMI_API_KEY", "sk-test")
    monkeypatch.setenv("CEREBRUM_FACTORY_LLM_MODEL", "kimi-k2.7-code")
    monkeypatch.setenv("CEREBRUM_FACTORY_LLM_FALLBACK_MODEL", "kimi-k2.5-code")


def test_chat_config_includes_fallback_model(chat_env):
    cfg = get_llm_config()
    assert cfg["model"] == "kimi-k2.7"
    assert cfg["fallback_model"] == "moonshot-v1-8k"


def test_factory_config_includes_code_fallback_model(factory_env):
    cfg = get_factory_llm_config()
    assert cfg["model"] == "kimi-k2.7-code"
    assert cfg["fallback_model"] == "kimi-k2.5-code"


@pytest.mark.anyio
async def test_chain_generator_retries_with_fallback_model():
    """Primary fails, fallback model is used for the retry."""
    primary_resp = MagicMock()
    primary_resp.raise_for_status.side_effect = httpx.HTTPError("primary down")
    fallback_resp = MagicMock()
    fallback_resp.raise_for_status.return_value = None
    fallback_resp.json.return_value = {
        "choices": [{"message": {"content": "{}"}}]
    }

    calls = []

    async def fake_post(self, url, *, json=None, headers=None):
        calls.append(json["model"])
        if json["model"] == "primary":
            return primary_resp
        return fallback_resp

    with patch.object(httpx.AsyncClient, "post", fake_post):
        result = await _call_openai_compatible(
            "https://api.test.kimi/v1",
            "sk-test",
            "primary",
            [{"role": "user", "content": "hi"}],
            fallback_model="fallback",
        )

    assert calls == ["primary", "fallback"]
    assert result == {}


@pytest.mark.anyio
async def test_chain_generator_does_not_retry_when_no_fallback():
    primary_resp = MagicMock()
    primary_resp.raise_for_status.side_effect = httpx.HTTPError("primary down")

    with patch.object(httpx.AsyncClient, "post", return_value=primary_resp):
        with pytest.raises(httpx.HTTPError):
            await _call_openai_compatible(
                "https://api.test.kimi/v1",
                "sk-test",
                "primary",
                [{"role": "user", "content": "hi"}],
            )


def test_product_architect_retries_with_fallback_model(factory_env):
    """Factory architect retries with the configured code fallback model."""
    primary_resp = MagicMock()
    primary_resp.raise_for_status.side_effect = httpx.HTTPError("primary down")
    fallback_resp = MagicMock()
    fallback_resp.raise_for_status.return_value = None
    fallback_resp.json.return_value = {
        "choices": [{"message": {"content": '{"ok": true}'}}]
    }

    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append(json["model"])
        if json["model"] == "kimi-k2.7-code":
            return primary_resp
        return fallback_resp

    with patch.object(httpx, "post", fake_post):
        result = _llm_json_call([{"role": "user", "content": "build"}])

    assert calls == ["kimi-k2.7-code", "kimi-k2.5-code"]
    assert result == {"ok": True}
