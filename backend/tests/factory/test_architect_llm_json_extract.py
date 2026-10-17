"""CEREBRUMDEV-BACKEND-D: Floor / architect JSON extract, not raw json.loads.

OpenRouter free models (provider still labeled kimi/moonshot, base_url
openrouter.ai) sometimes return safety prose such as ``User Safety: safe``
instead of a JSON object. ``_llm_json_call`` must use ``_extract_json`` so
that fails as ValueError and Floor regex / keyword fallbacks stay clean.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.factory.product_architect import LlmSoftMiss, _extract_json, _llm_json_call


_SENTRY_SAFETY = "User Safety: safe"


def test_extract_json_sentry_safety_prose_is_valueerror():
    with pytest.raises(ValueError, match="No JSON object found in model output"):
        _extract_json(_SENTRY_SAFETY)


def test_extract_json_empty_string_is_soft_miss():
    with pytest.raises(LlmSoftMiss, match="Empty model output"):
        _extract_json("")


def test_extract_json_whitespace_is_soft_miss():
    with pytest.raises(LlmSoftMiss, match="Empty model output"):
        _extract_json("  \n\t  ")


def test_extract_json_empty_object_is_soft_miss():
    with pytest.raises(LlmSoftMiss, match="Empty JSON object"):
        _extract_json("{}")


def test_extract_json_none_is_soft_miss():
    with pytest.raises(LlmSoftMiss, match="Empty model output"):
        _extract_json(None)  # type: ignore[arg-type]


def test_extract_json_does_not_raise_json_decode_error_on_prose():
    try:
        _extract_json(_SENTRY_SAFETY)
    except json.JSONDecodeError:
        pytest.fail("safety prose must not reach json.loads")
    except ValueError:
        return
    pytest.fail("expected ValueError for non-JSON model output")


def test_extract_json_first_object_and_fences():
    fenced = '```json\n{"action": "reply", "message": "ok"}\n```'
    assert _extract_json(fenced) == {"action": "reply", "message": "ok"}
    preamble = 'Sure.\n{"product_name": "Harbor", "capabilities": []}\nthanks'
    assert _extract_json(preamble)["product_name"] == "Harbor"


def _openrouter_factory_env(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "kimi")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("CEREBRUM_FACTORY_LLM_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("CEREBRUM_LLM_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("CEREBRUM_FACTORY_LLM_MODEL", "openrouter/free")
    monkeypatch.setenv("CEREBRUM_FACTORY_LLM_FALLBACK_MODEL", "openrouter/free")
    monkeypatch.delenv("KIMI_API_KEY", raising=False)
    monkeypatch.delenv("CEREBRUM_LLM_API_KEY", raising=False)
    monkeypatch.delenv("CEREBRUM_FACTORY_LLM_API_KEY", raising=False)
    monkeypatch.delenv("KIMI_MOCK", raising=False)
    monkeypatch.delenv("CEREBRUM_LLM_MOCK", raising=False)


def _completion(content):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"choices": [{"message": {"content": content}}]}
    return resp


def test_llm_json_call_openrouter_safety_prose_is_valueerror(monkeypatch):
    """Live Sentry CEREBRUMDEV-BACKEND-D content must not JSONDecodeError."""
    _openrouter_factory_env(monkeypatch)
    with patch.object(httpx, "post", return_value=_completion(_SENTRY_SAFETY)):
        with pytest.raises(ValueError, match="No JSON object found in model output") as excinfo:
            _llm_json_call([{"role": "user", "content": "approve"}])
    assert not isinstance(excinfo.value, json.JSONDecodeError)


def test_llm_json_call_empty_content_is_soft_miss(monkeypatch):
    _openrouter_factory_env(monkeypatch)
    with patch.object(httpx, "post", return_value=_completion("")):
        with pytest.raises(LlmSoftMiss, match="Empty model output"):
            _llm_json_call([{"role": "user", "content": "approve"}])


def test_llm_json_call_empty_object_is_soft_miss(monkeypatch):
    _openrouter_factory_env(monkeypatch)
    with patch.object(httpx, "post", return_value=_completion("{}")):
        with pytest.raises(LlmSoftMiss, match="Empty JSON object"):
            _llm_json_call([{"role": "user", "content": "approve"}])


def test_llm_json_call_parses_fenced_json(monkeypatch):
    _openrouter_factory_env(monkeypatch)
    body = '```json\n{"action": "reply", "brief": "", "message": "ok"}\n```'
    with patch.object(httpx, "post", return_value=_completion(body)):
        assert _llm_json_call([{"role": "user", "content": "hi"}])["action"] == "reply"
