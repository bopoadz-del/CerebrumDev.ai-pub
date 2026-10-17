"""One provider ladder for the Factory's model calls (owner decision 3).

Live 2026-10-06 (dd1b353d): DeepSeek answered 402 Payment Required on every
call; the Floor fell back and told the user the chat model was "not
configured on this deployment" -- it was configured, and refusing payment.

* (a) a 402/429/5xx on one provider moves the call to the next rung, and each
  failover is recorded (a ledger NOTE inside a build);
* (b) the Floor says WHY by the typed kind, never "not configured" when a
  provider is configured.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.core import llm_config, model_ladder
from app.core.model_ladder import (
    BAD_REQUEST,
    NOT_CONFIGURED,
    PAYMENT_REFUSED,
    RATE_LIMITED,
    UNAVAILABLE,
    ModelUnavailable,
)
from app.factory import llm_watchdog, product_architect

PRIMARY = {
    "provider": "deepseek",
    "base_url": "https://primary.invalid/v1",
    "api_key": "k-primary",
    "model": "primary-model",
    "fallback_model": "primary-model",
}
LEG = {
    "provider": "openrouter",
    "base_url": "https://leg.invalid/api/v1",
    "api_key": "k-leg",
    "model": "leg-model:free",
    "is_free": True,
}


def _resp(url: str, status: int, body=None) -> httpx.Response:
    request = httpx.Request("POST", url)
    if body is None:
        body = {"choices": [{"message": {"content": json.dumps({"ok": True, "via": url})}}]}
    return httpx.Response(status, json=body, request=request)


@pytest.fixture
def wire(monkeypatch):
    """Stub the network: ``plan[host] = [status, ...]`` per call to that host."""
    plan: dict = {}
    calls: list = []

    def _post(url, json=None, headers=None, timeout=None, **_k):  # noqa: A002
        host = httpx.URL(url).host
        calls.append(host)
        statuses = plan.get(host) or [200]
        status = statuses.pop(0) if len(statuses) > 1 else statuses[0]
        return _resp(url, status)

    monkeypatch.setattr(llm_watchdog, "post_with_deadline", _post)
    monkeypatch.setattr(product_architect, "get_factory_llm_config", lambda: dict(PRIMARY))
    monkeypatch.setattr(product_architect, "get_llm_config", lambda: dict(PRIMARY))
    monkeypatch.setattr(llm_config, "get_factory_fallback_leg", lambda: dict(LEG))
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    return plan, calls


def test_payment_refusal_fails_over_and_the_ledger_records_it(wire):
    from app.factory.build.architect import architect_call

    plan, calls = wire
    plan["primary.invalid"] = [402]
    notes: list = []
    out = architect_call(
        [{"role": "user", "content": "x"}],
        note=lambda detail, **payload: notes.append((detail, payload)),
    )
    assert out["ok"] is True and "leg.invalid" in out["via"]
    assert calls == ["primary.invalid", "leg.invalid"]  # no same-endpoint retry after a 402
    assert len(notes) == 1
    detail, payload = notes[0]
    assert payload["source"] == "model_ladder"
    assert payload["failover"]["kind"] == PAYMENT_REFUSED
    assert payload["failover"]["status"] == 402
    assert payload["failover"]["provider"] == "deepseek"
    assert payload["failover"]["next_provider"] == "openrouter"


def test_rate_limit_fails_over_with_its_kind(wire):
    plan, calls = wire
    plan["primary.invalid"] = [429]
    events: list = []
    with model_ladder.failover_to(events.append):
        out = product_architect._llm_json_call([{"role": "user", "content": "x"}])
    assert "leg.invalid" in out["via"]
    assert [e["kind"] for e in events] == [RATE_LIMITED]


def test_server_error_fails_over_as_unavailable(wire):
    plan, _calls = wire
    plan["primary.invalid"] = [503]
    events: list = []
    with model_ladder.failover_to(events.append):
        product_architect._llm_json_call([{"role": "user", "content": "x"}])
    assert [e["kind"] for e in events] == [UNAVAILABLE]


def test_a_bad_request_does_not_fail_over(wire):
    plan, calls = wire
    plan["primary.invalid"] = [400]
    with pytest.raises(ModelUnavailable) as err:
        product_architect._llm_json_call([{"role": "user", "content": "x"}])
    assert err.value.kind == BAD_REQUEST
    assert "leg.invalid" not in calls


def test_every_rung_refusing_raises_the_last_kind(wire):
    plan, calls = wire
    plan["primary.invalid"] = [402]
    plan["leg.invalid"] = [402]
    with pytest.raises(ModelUnavailable) as err:
        product_architect._llm_json_call([{"role": "user", "content": "x"}])
    assert err.value.kind == PAYMENT_REFUSED
    assert calls == ["primary.invalid", "leg.invalid"]
    assert [a["provider"] for a in err.value.attempts] == ["deepseek", "openrouter"]


def test_the_ladder_follows_config_order(monkeypatch):
    monkeypatch.setattr(llm_config, "get_factory_fallback_leg", lambda: dict(LEG))
    rungs = model_ladder.rungs_from_config(dict(PRIMARY))
    assert [r.provider for r in rungs] == ["deepseek", "openrouter"]
    assert [r.base_url for r in rungs] == [PRIMARY["base_url"], LEG["base_url"]]
    # Each rung carries its OWN key -- never the primary's.
    assert rungs[1].api_key == LEG["api_key"]


def test_an_unarmed_or_misconfigured_leg_is_skipped_not_an_error(monkeypatch):
    monkeypatch.setattr(llm_config, "get_factory_fallback_leg", lambda: None)
    assert [r.provider for r in model_ladder.rungs_from_config(dict(PRIMARY))] == ["deepseek"]
    monkeypatch.setattr(
        llm_config, "get_factory_fallback_leg", lambda: {**LEG, "error": "paid model not allowed"}
    )
    assert [r.provider for r in model_ladder.rungs_from_config(dict(PRIMARY))] == ["deepseek"]


def test_nothing_configured_is_not_configured(monkeypatch):
    monkeypatch.setattr(llm_config, "get_factory_fallback_leg", lambda: None)
    monkeypatch.setattr(
        product_architect, "get_factory_llm_config", lambda: {"error": "no factory LLM key"}
    )
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    with pytest.raises(ModelUnavailable) as err:
        product_architect._llm_json_call([{"role": "user", "content": "x"}])
    assert err.value.kind == NOT_CONFIGURED


def test_the_floor_phrase_follows_the_kind():
    assert "refused payment" in model_ladder.floor_unavailable_reason(PAYMENT_REFUSED)
    assert "rate-limiting" in model_ladder.floor_unavailable_reason(RATE_LIMITED)
    assert "unavailable" in model_ladder.floor_unavailable_reason(UNAVAILABLE)
    for kind in (PAYMENT_REFUSED, RATE_LIMITED, UNAVAILABLE, BAD_REQUEST):
        assert "not configured" not in model_ladder.floor_unavailable_reason(kind)
    assert "not configured" in model_ladder.floor_unavailable_reason(None)


# --- (b) end to end through the Floor chat router ---------------------------


@pytest.fixture
def session():
    from app.core import session_store
    from app.models.session import ProductDesignState, SessionState

    state = SessionState(session_id="sess-ladder", user_id="user-1", account_id="acct-1")
    state.product_design = ProductDesignState()
    session_store._session_store[state.session_id] = state
    yield state
    session_store._session_store.pop(state.session_id, None)


async def _summary(session_id: str, message: str) -> str:
    from app.routers import chat as chat_router

    text = []
    async for raw in chat_router._stream_response(session_id, message, None, None):
        event, data = "", ""
        for line in [l for l in raw.strip().splitlines() if l]:
            if line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data = json.loads(line.split(":", 1)[1].strip())
        if event == "info":
            payload = json.loads(data) if isinstance(data, str) else data
            text.append(str((payload or {}).get("summary") or ""))
    return " ".join(text)


@pytest.mark.asyncio
async def test_the_floor_says_the_provider_refused_payment(session, monkeypatch):
    from app.factory import platform_chat_llm

    def _refused(*_a, **_k):
        raise ModelUnavailable(PAYMENT_REFUSED, "model provider deepseek refused (payment_refused, HTTP 402)")

    monkeypatch.setattr(platform_chat_llm, "should_orchestrate", lambda *a, **k: True)
    monkeypatch.setattr(platform_chat_llm, "decide", _refused)
    summary = await _summary(session.session_id, "build me a tool library")
    assert "refused payment" in summary
    assert "not configured" not in summary


@pytest.mark.asyncio
async def test_the_floor_says_not_configured_only_when_nothing_is(session, monkeypatch):
    from app.factory import platform_chat_llm

    monkeypatch.setattr(platform_chat_llm, "should_orchestrate", lambda *a, **k: False)
    summary = await _summary(session.session_id, "build me a tool library")
    assert "not configured" in summary
