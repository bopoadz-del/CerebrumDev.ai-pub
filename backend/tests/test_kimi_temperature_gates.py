"""Temperature-gate regression tests for the OpenAI-compatible LLM paths.

Reasoning models (e.g. the k2-class reasoning models) reject any explicit
temperature other than 1. The Factory therefore omits temperature from
the API payload unless LLM_TEMPERATURE is explicitly set.
"""

import os
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.core.chain_generator import _call_openai_compatible
from app.core.llm_config import get_factory_llm_config, get_llm_config
from app.factory.product_architect import _llm_json_call


@pytest.fixture(autouse=True)
def _clear_temperature():
    old = os.environ.get("LLM_TEMPERATURE")
    os.environ.pop("LLM_TEMPERATURE", None)
    yield
    if old is None:
        os.environ.pop("LLM_TEMPERATURE", None)
    else:
        os.environ["LLM_TEMPERATURE"] = old


@pytest.fixture
def primary_env(monkeypatch):
    """Set a minimal provider-table configuration for both chat and factory paths."""
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("CEREBRUM_LLM_API_KEY", "sk-test")
    monkeypatch.setenv("CEREBRUM_LLM_BASE_URL", "https://api.test.example/v1")
    monkeypatch.setenv("CEREBRUM_CHAT_LLM_MODEL", "chat-model")
    monkeypatch.setenv("CEREBRUM_FACTORY_LLM_MODEL", "code-model")


def test_llm_config_temperature_omitted_by_default(primary_env):
    chat_cfg = get_llm_config()
    factory_cfg = get_factory_llm_config()
    assert chat_cfg["temperature"] is None
    assert factory_cfg["temperature"] is None


def test_llm_config_temperature_honored_when_set(primary_env, monkeypatch):
    monkeypatch.setenv("LLM_TEMPERATURE", "0.4")
    chat_cfg = get_llm_config()
    factory_cfg = get_factory_llm_config()
    assert chat_cfg["temperature"] == 0.4
    assert factory_cfg["temperature"] == 0.4


@pytest.mark.anyio
async def test_chain_generator_omits_temperature_for_kimi():
    """Async chat/chain path must not send temperature when it is None."""
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "choices": [{"message": {"content": "{}"}}]
    }

    with patch.object(httpx.AsyncClient, "post", return_value=response) as mock_post:
        await _call_openai_compatible(
            "https://api.test.example/v1",
            "sk-test",
            "chat-model",
            [{"role": "user", "content": "hi"}],
            temperature=None,
        )

    payload = mock_post.call_args.kwargs["json"]
    assert "temperature" not in payload
    assert payload["model"] == "chat-model"


@pytest.mark.anyio
async def test_chain_generator_includes_temperature_when_configured():
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "choices": [{"message": {"content": "{}"}}]
    }

    with patch.object(httpx.AsyncClient, "post", return_value=response) as mock_post:
        await _call_openai_compatible(
            "https://api.test.example/v1",
            "sk-test",
            "chat-model",
            [{"role": "user", "content": "hi"}],
            temperature=0.5,
        )

    payload = mock_post.call_args.kwargs["json"]
    assert payload["temperature"] == 0.5


def test_product_architect_omits_temperature_for_kimi(primary_env):
    """Factory Product Architect path must not send temperature when it is None."""
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "choices": [{"message": {"content": '{"ok": true}'}}]
    }

    with patch.object(httpx, "post", return_value=response) as mock_post:
        _llm_json_call([{"role": "user", "content": "build a thing"}])

    payload = mock_post.call_args.kwargs["json"]
    assert "temperature" not in payload
    assert payload["model"] == "code-model"


def test_product_architect_includes_temperature_when_configured(primary_env, monkeypatch):
    monkeypatch.setenv("LLM_TEMPERATURE", "0.6")
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "choices": [{"message": {"content": '{"ok": true}'}}]
    }

    with patch.object(httpx, "post", return_value=response) as mock_post:
        _llm_json_call([{"role": "user", "content": "build a thing"}])

    payload = mock_post.call_args.kwargs["json"]
    assert payload["temperature"] == 0.6
