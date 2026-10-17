"""The coder must not send a temperature the model will reject.

New-shape tests for a defect that made the factory silently useless on every
capable model. `coder.py` hardcoded `"temperature": 0.2`; kimi-k2.x and k3
answer `400 invalid temperature: only 1 is allowed for this model`. The coder
caught the HTTPStatusError, recorded a CoderError, and shipped the
deterministic template -- so a build looked successful, reported
`agent_written: 0`, and the operator's conclusion was "the model is too weak"
when the request never reached the model at all.

Measured before and after on the same blueprint:

    kimi-k2.7-code, hardcoded 0.2  -> 400 on every call, 0 agent artifacts
    kimi-k2.7-code, temperature omitted -> SUCCESS, rework 0, 7 agent artifacts

`llm_config._llm_temperature()` already returned None by default for exactly
this reason and documented it. The coder was the one caller ignoring it, so
these tests pin the wire payload rather than the config.
"""

from __future__ import annotations

import pytest

import app.factory.coder as coder

FAKE_KIMI = "sk-kimi-not-a-real-key"
FAKE_CLAUDE = "sk-ant-not-a-real-key"


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


@pytest.fixture()
def sent(monkeypatch):
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured.update(url=url, json=json, headers=headers)
        if "/messages" in url:
            return _Resp({"content": [{"type": "text", "text": "return {}"}]})
        return _Resp({"choices": [{"message": {"content": "return {}"}}]})

    monkeypatch.setattr(coder.httpx, "post", fake_post)
    return captured


def _clear(monkeypatch):
    for var in (
        "LLM_TEMPERATURE",
        "CEREBRUM_FACTORY_LLM_MODEL",
        "CEREBRUM_LLM_MODEL",
        "KIMI_MODEL",
        "ANTHROPIC_MODEL",
    ):
        monkeypatch.delenv(var, raising=False)


def test_no_temperature_is_sent_when_none_is_configured(monkeypatch, sent):
    """The reasoning-model case. A 0.2 here is a hard 400."""
    _clear(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "kimi")
    monkeypatch.setenv("KIMI_API_KEY", FAKE_KIMI)

    coder._llm_code_call([{"role": "user", "content": "hi"}])

    assert "temperature" not in sent["json"], (
        "a temperature was sent with none configured — kimi-k2.x/k3 reject "
        "any value but 1 and the coder falls back to templates"
    )
    assert sent["json"]["max_tokens"] == coder.code_max_tokens()


def test_the_completion_budget_is_configurable_and_generous(monkeypatch, sent):
    """2048 starved reasoning models: they spend it on reasoning tokens and
    return finish_reason="length" with empty content, which the coder then
    reported as a bare "empty completion" and dropped to the template."""
    _clear(monkeypatch)
    monkeypatch.delenv("FACTORY_CODER_MAX_TOKENS", raising=False)
    assert coder.code_max_tokens() >= 4096

    monkeypatch.setenv("FACTORY_CODER_MAX_TOKENS", "1234")
    assert coder.code_max_tokens() == 1234
    monkeypatch.setenv("FACTORY_CODER_MAX_TOKENS", "not-a-number")
    assert coder.code_max_tokens() >= 4096


def test_an_empty_completion_says_why(monkeypatch):
    """A bare "empty completion" costs a live debugging run to interpret."""
    _clear(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "kimi")
    monkeypatch.setenv("KIMI_API_KEY", FAKE_KIMI)

    class _Empty:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [
                    {"message": {"content": "", "reasoning_content": "x" * 40},
                     "finish_reason": "length"}
                ],
                "usage": {"completion_tokens": 8192},
            }

    monkeypatch.setattr(coder.httpx, "post", lambda *a, **k: _Empty())

    with pytest.raises(coder.CoderError) as exc:
        coder._llm_code_call([{"role": "user", "content": "hi"}])
    message = str(exc.value)
    assert "finish_reason='length'" in message
    assert "completion_tokens=8192" in message
    assert "reasoning_chars=40" in message


def test_an_explicit_temperature_is_still_honoured(monkeypatch, sent):
    """Omitting by default must not mean the setting is ignored."""
    _clear(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "kimi")
    monkeypatch.setenv("KIMI_API_KEY", FAKE_KIMI)
    monkeypatch.setenv("LLM_TEMPERATURE", "0.4")

    coder._llm_code_call([{"role": "user", "content": "hi"}])

    assert sent["json"]["temperature"] == 0.4


def test_the_anthropic_path_omits_it_too(monkeypatch, sent):
    _clear(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "claude")
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_CLAUDE)

    coder._llm_code_call(
        [{"role": "system", "content": "S"}, {"role": "user", "content": "U"}]
    )

    assert sent["url"].endswith("/messages")
    assert "temperature" not in sent["json"]
    assert sent["json"]["system"] == "S"


def test_the_anthropic_path_honours_an_explicit_temperature(monkeypatch, sent):
    _clear(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "claude")
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_CLAUDE)
    monkeypatch.setenv("LLM_TEMPERATURE", "0.7")

    coder._llm_code_call([{"role": "user", "content": "U"}])

    assert sent["json"]["temperature"] == 0.7


def test_the_factory_fallback_model_is_one_that_exists(monkeypatch):
    """kimi-k2.5-code answered 404 'Not found the model'.

    A fallback that cannot resolve means a primary failure surfaces as two
    errors instead of one retry, and the retry leg was never real.
    """
    from app.core.llm_config import get_factory_llm_config

    for var in ("LLM_PROVIDER", "CEREBRUM_FACTORY_LLM_FALLBACK_MODEL",
                "KIMI_FALLBACK_MODEL", "CEREBRUM_LLM_FALLBACK_MODEL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("KIMI_API_KEY", FAKE_KIMI)

    fallback = get_factory_llm_config()["fallback_model"]
    assert fallback != "kimi-k2.5-code", "that model does not exist on the API"
    # Models the endpoint actually serves, as listed by GET /v1/models.
    assert fallback in {
        "kimi-k2.5",
        "kimi-k2.6",
        "kimi-k2.7-code",
        "kimi-k2.7-code-highspeed",
        "kimi-k3",
        "moonshot-v1-8k",
        "moonshot-v1-32k",
        "moonshot-v1-128k",
        "moonshot-v1-auto",
    }, fallback


def test_the_fallback_leg_is_actually_tried(monkeypatch):
    """Primary fails, fallback answers -- one retry, not two errors."""
    _clear(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "kimi")
    monkeypatch.setenv("KIMI_API_KEY", FAKE_KIMI)
    monkeypatch.setenv("CEREBRUM_FACTORY_LLM_MODEL", "primary-model")
    monkeypatch.setenv("CEREBRUM_FACTORY_LLM_FALLBACK_MODEL", "fallback-model")

    tried = []

    def fake_post(url, json=None, headers=None, timeout=None):
        tried.append(json["model"])
        if json["model"] == "primary-model":
            raise RuntimeError("primary is down")
        return _Resp({"choices": [{"message": {"content": "return {}"}}]})

    monkeypatch.setattr(coder.httpx, "post", fake_post)

    text, model_used = coder._llm_code_call([{"role": "user", "content": "hi"}])
    assert text == "return {}"
    # The fallback leg answered, so the fallback model is what provenance must
    # record -- naming the primary here is how a build claims an author it
    # never had.
    assert model_used == "fallback-model"
    assert tried == ["primary-model", "fallback-model"]
