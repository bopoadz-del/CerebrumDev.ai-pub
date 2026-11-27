"""The architect's draft call on a reasoning model: an empty reply that stopped
at the length limit is "ran out of allowance", retried once with a larger
allowance; any other empty reply is not retried.

Decided only by what the provider reports (finish_reason, content), never by
the model's name.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.factory import product_architect
from app.factory.product_architect import (
    AllowanceExhausted,
    LlmSoftMiss,
    _llm_json_call,
    allowance_exhausted,
)


def _env(monkeypatch, allowance: str | None) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("CEREBRUM_FACTORY_LLM_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("CEREBRUM_LLM_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("CEREBRUM_FACTORY_LLM_MODEL", "openrouter/free")
    monkeypatch.setenv("CEREBRUM_FACTORY_LLM_FALLBACK_MODEL", "openrouter/free")
    monkeypatch.delenv("CEREBRUM_LLM_API_KEY", raising=False)
    monkeypatch.delenv("CEREBRUM_FACTORY_LLM_API_KEY", raising=False)
    monkeypatch.delenv("CEREBRUM_LLM_MOCK", raising=False)
    if allowance is None:
        monkeypatch.delenv("ARCHITECT_LLM_MAX_TOKENS", raising=False)
    else:
        monkeypatch.setenv("ARCHITECT_LLM_MAX_TOKENS", allowance)


def _reply(content: Any, finish: str, completion_tokens: int = 0) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {
        "choices": [{"message": {"content": content}, "finish_reason": finish}],
        "usage": {
            "completion_tokens": completion_tokens,
            "completion_tokens_details": {"reasoning_tokens": completion_tokens},
        },
    }
    return resp


def _run(replies: List[MagicMock]):
    sent: List[Dict[str, Any]] = []

    def fake_post(url, json=None, **_kw):
        sent.append(json)
        return replies[len(sent) - 1]

    with patch.object(httpx, "post", side_effect=fake_post):
        try:
            return _llm_json_call([{"role": "user", "content": "draft"}]), sent
        except Exception as exc:  # returned so the test can read `sent` too
            return exc, sent


def test_default_allowance_fits_a_reasoning_model(monkeypatch):
    _env(monkeypatch, None)
    out, sent = _run([_reply('{"product_name": "x"}', "stop")])
    assert out == {"product_name": "x"}
    assert sent[0]["max_tokens"] == 8192
    assert product_architect.llm_max_tokens() == 8192


@pytest.mark.parametrize("allowance", ["777", "3000"])
def test_request_carries_the_configured_allowance(monkeypatch, allowance):
    _env(monkeypatch, allowance)
    _out, sent = _run([_reply('{"a": 1}', "stop")])
    assert [p["max_tokens"] for p in sent] == [int(allowance)]


@pytest.mark.parametrize(
    "allowance, empty, answer",
    [("1000", "", '{"product_name": "Ledger"}'), ("4096", None, '{"vertical": "clinic"}')],
)
def test_empty_reply_at_length_limit_retries_once_with_double(monkeypatch, allowance, empty, answer):
    _env(monkeypatch, allowance)
    out, sent = _run([_reply(empty, "length", int(allowance)), _reply(answer, "stop", 300)])
    assert [p["max_tokens"] for p in sent] == [int(allowance), 2 * int(allowance)]
    assert out == json.loads(answer)


@pytest.mark.parametrize("allowance", ["1000", "2500"])
def test_still_empty_after_retry_is_reported_as_out_of_allowance(monkeypatch, allowance):
    _env(monkeypatch, allowance)
    out, sent = _run([_reply("", "length", int(allowance)), _reply("  ", "length", 2 * int(allowance))])
    assert isinstance(out, AllowanceExhausted)
    assert isinstance(out, LlmSoftMiss)
    assert "ran out of allowance" in str(out)
    assert f"max_tokens={2 * int(allowance)}" in str(out)
    assert f"completion_tokens={2 * int(allowance)}" in str(out)
    assert len(sent) == 2


@pytest.mark.parametrize("empty, finish", [("", "stop"), (None, "content_filter")])
def test_empty_reply_for_another_reason_is_not_retried(monkeypatch, empty, finish):
    _env(monkeypatch, "1000")
    out, sent = _run([_reply(empty, finish), _reply('{"a": 1}', "stop")])
    assert isinstance(out, LlmSoftMiss)
    assert not isinstance(out, AllowanceExhausted)
    assert "Empty model output" in str(out)
    assert len(sent) == 1


@pytest.mark.parametrize("content, expected", [('{"a": 1}', {"a": 1}), ('ok {"b": 2}', {"b": 2})])
def test_content_at_length_limit_is_not_retried(monkeypatch, content, expected):
    _env(monkeypatch, "1000")
    out, sent = _run([_reply(content, "length"), _reply('{"z": 0}', "stop")])
    assert out == expected
    assert len(sent) == 1


@pytest.mark.parametrize(
    "choice, expected",
    [
        ({"finish_reason": "length", "message": {"content": ""}}, True),
        ({"finish_reason": "length", "message": {"content": None}}, True),
        ({"finish_reason": "length", "message": {"content": " \n"}}, True),
        ({"finish_reason": "length"}, True),
        ({"finish_reason": "length", "message": {"content": "{}"}}, False),
        ({"finish_reason": "stop", "message": {"content": ""}}, False),
        ({"message": {"content": ""}}, False),
        (None, False),
        ([], False),
    ],
)
def test_allowance_exhausted_reads_the_reply_only(choice, expected):
    assert allowance_exhausted(choice) is expected


@pytest.mark.parametrize("allowance, primary, fallback", [("1000", "m-a", "m-b"), ("3000", "x-1", "x-2")])
def test_fallback_model_gets_the_same_one_retry(monkeypatch, allowance, primary, fallback):
    _env(monkeypatch, allowance)
    monkeypatch.setenv("CEREBRUM_FACTORY_LLM_MODEL", primary)
    monkeypatch.setenv("CEREBRUM_FACTORY_LLM_FALLBACK_MODEL", fallback)
    n = int(allowance)
    out, sent = _run(
        [_reply("", "length", n), _reply("", "length", 2 * n), _reply(None, "length", n), _reply('{"k": 3}', "stop")]
    )
    assert out == {"k": 3}
    assert [p["model"] for p in sent] == [primary, primary, fallback, fallback]
    assert [p["max_tokens"] for p in sent] == [n, 2 * n, n, 2 * n]
