"""WK-2: behaviour of each provider-config helper the PR touches.

Each test pins what one helper decides for two or more settings, through the
helper itself, so a change to its rule (a dropped branch, a swallowed
refusal, a constant answer) changes an asserted outcome. Keys are generated
per test; no value here names a product, a probe or a vendor's secret.
"""

from __future__ import annotations

import asyncio
import os
import re
import secrets

import pytest

from app.core import chain_generator, llm_config, rule_parser
from app.factory import product_architect

_STEERING = re.compile(
    r"^(LLM_|ANTHROPIC_|CLAUDE_|CURSOR_|DEEPSEEK_|OPENROUTER_|"
    r"FACTORY_LLM_|FACTORY_CURSOR_|FACTORY_CODE_|CEREBRUM_)"
)

_OPENROUTER = llm_config.OPENROUTER_BASE_URL
_CURSOR = llm_config.CURSOR_AGENTS_BASE_URL
_OWN_HOST = "https://llm.internal.test/v1"


def _key() -> str:
    return "sk-" + secrets.token_hex(12)


@pytest.fixture
def env(monkeypatch):
    for name in list(os.environ):
        if _STEERING.match(name):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("FACTORY_LLM_FALLBACK_PROVIDER", "none")

    def _set(**values: str) -> None:
        for name, value in values.items():
            monkeypatch.setenv(name, value)

    return _set


# -- shared-slot resolution ----------------------------------------------------


@pytest.mark.parametrize(
    ("slots", "want"),
    [
        ({"CEREBRUM_CHAT_LLM_API_KEY": "k-chat", "CEREBRUM_LLM_API_KEY": "k-shared"}, "k-chat"),
        ({"CEREBRUM_LLM_API_KEY": "k-shared"}, "k-shared"),
        ({}, ""),
    ],
)
def test_shared_key_reads_the_prefixed_slot_then_the_shared_one(env, slots, want):
    env(**slots)
    assert llm_config._shared_key("CEREBRUM_CHAT") == want


@pytest.mark.parametrize(
    ("base", "want"),
    [(_OWN_HOST, "k-shared"), (_OPENROUTER, "")],
)
def test_native_shared_key_skips_a_slot_pointed_at_openrouter(env, base, want):
    env(CEREBRUM_LLM_API_KEY="k-shared", CEREBRUM_LLM_BASE_URL=base)
    assert llm_config._native_shared_key("CEREBRUM_CHAT") == want


@pytest.mark.parametrize(
    ("skip", "want"),
    [(False, "m-on-openrouter"), (True, llm_config.DEFAULT_PRIMARY_MODEL)],
)
def test_shared_model_skips_an_openrouter_slot_only_when_asked(env, skip, want):
    env(CEREBRUM_LLM_MODEL="m-on-openrouter", CEREBRUM_LLM_BASE_URL=_OPENROUTER)
    assert llm_config._shared_model("CEREBRUM_CHAT", skip_openrouter=skip) == want


@pytest.mark.parametrize(
    ("skip", "want"),
    [(False, "fb-on-openrouter"), (True, "fb-default")],
)
def test_shared_fallback_model_skips_an_openrouter_slot_only_when_asked(env, skip, want):
    env(CEREBRUM_LLM_FALLBACK_MODEL="fb-on-openrouter", CEREBRUM_LLM_BASE_URL=_OPENROUTER)
    got = llm_config._shared_fallback_model(
        "CEREBRUM_CHAT", default="fb-default", skip_openrouter=skip
    )
    assert got == want


@pytest.mark.parametrize(
    ("shared_fallback", "want"),
    [(None, llm_config.DEFAULT_FALLBACK_MODEL), ("fb-set", "fb-set")],
)
def test_a_scoped_endpoint_takes_the_default_fallback_model_when_none_is_set(
    env, shared_fallback, want
):
    env(CEREBRUM_CHAT_LLM_API_KEY=_key())
    if shared_fallback:
        env(CEREBRUM_LLM_FALLBACK_MODEL=shared_fallback)
    assert llm_config._scoped_path_endpoint("CEREBRUM_CHAT")["fallback_model"] == want


@pytest.mark.parametrize(
    ("chat_base", "want"),
    [(_CURSOR, llm_config.DEEPSEEK_BASE_URL), (_OWN_HOST, _OWN_HOST)],
)
def test_a_present_scoped_key_owns_the_endpoint_and_never_the_cursor_host(env, chat_base, want):
    key = _key()
    env(CEREBRUM_CHAT_LLM_API_KEY=key, CEREBRUM_CHAT_LLM_BASE_URL=chat_base)
    got = llm_config._resolve_shared_primary("CEREBRUM_CHAT")
    assert (got["api_key"], got["base_url"]) == (key, want)


@pytest.mark.parametrize(
    ("own_fallback", "want"),
    [(None, llm_config.DEFAULT_FALLBACK_MODEL), ("fb-own", "fb-own")],
)
def test_deepseek_scoped_config_ignores_the_shared_fallback_slot(env, own_fallback, want):
    env(CEREBRUM_FACTORY_LLM_API_KEY=_key(), CEREBRUM_LLM_FALLBACK_MODEL="fb-shared")
    if own_fallback:
        env(CEREBRUM_FACTORY_LLM_FALLBACK_MODEL=own_fallback)
    cfg = llm_config._deepseek_config("CEREBRUM_FACTORY")
    assert (cfg["provider"], cfg["fallback_model"]) == ("deepseek", want)


# -- claude key, mock switch, temperature -------------------------------------


@pytest.mark.parametrize(
    ("declared", "want_key"),
    [("claude", True), ("", False)],
)
def test_a_declared_claude_owns_its_key_even_beside_an_openrouter_base(env, declared, want_key):
    key = _key()
    env(ANTHROPIC_API_KEY=key, ANTHROPIC_BASE_URL=_OPENROUTER)
    if declared:
        env(LLM_PROVIDER=declared)
    assert llm_config._native_claude_key() == (key if want_key else "")


@pytest.mark.parametrize(("flag", "want"), [("1", True), ("0", False)])
def test_llm_mock_enabled_reads_the_offline_switch(env, flag, want):
    env(CEREBRUM_LLM_MOCK=flag)
    assert llm_config.llm_mock_enabled() is want


@pytest.mark.parametrize(("raw", "want"), [("0.3", 0.3), ("1", 1.0), ("warm", None)])
def test_llm_temperature_parses_the_setting(env, raw, want):
    env(LLM_TEMPERATURE=raw)
    assert llm_config._llm_temperature() == want


@pytest.fixture
def temperature_log(env, monkeypatch, caplog):
    monkeypatch.setattr(llm_config, "_temperature_reported", False)
    caplog.set_level("WARNING", logger=llm_config.logger.name)

    def _warnings() -> list[str]:
        return [r.getMessage() for r in caplog.records if r.name == llm_config.logger.name]

    return _warnings


@pytest.mark.parametrize(
    ("raw", "want", "reported"),
    [("", None, False), ("   ", None, False), ("0.2", 0.2, False), ("abc", None, True)],
)
def test_an_invalid_temperature_is_reported_and_an_empty_one_is_not(
    env, temperature_log, raw, want, reported
):
    env(LLM_TEMPERATURE=raw)
    assert llm_config._llm_temperature() == want
    warnings = temperature_log()
    assert len(warnings) == (1 if reported else 0)
    error = llm_config.temperature_setting_error()
    assert bool(error) is reported
    assert llm_config.llm_ready_details()["llm_temperature_error"] == error
    if reported:
        assert warnings == [error]
        assert "LLM_TEMPERATURE" in error
        assert raw not in error


@pytest.mark.parametrize("reads", [1, 3])
def test_an_unset_temperature_has_no_error(env, temperature_log, reads):
    for _ in range(reads):
        assert llm_config._llm_temperature() is None
    assert llm_config.temperature_setting_error() == ""
    assert temperature_log() == []


@pytest.mark.parametrize("raw, reads", [("abc", 2), ("warm", 5)])
def test_an_invalid_temperature_warns_once_per_process(env, temperature_log, raw, reads):
    env(LLM_TEMPERATURE=raw)
    for _ in range(reads):
        assert llm_config._llm_temperature() is None
    assert len(temperature_log()) == 1


@pytest.mark.parametrize("name", ["openrouter", "nope"])
def test_config_for_a_name_outside_the_primary_rows_raises(env, name):
    with pytest.raises(RuntimeError, match=name):
        llm_config._config_for_provider(name)


@pytest.mark.parametrize("off", ["none", "off", "disabled"])
def test_a_disabled_fallback_leg_is_none_even_with_its_key_set(env, off):
    env(FACTORY_LLM_FALLBACK_PROVIDER=off, OPENROUTER_API_KEY=_key())
    assert llm_config.get_factory_fallback_leg() is None


# -- chat path (chain_generator) ----------------------------------------------


@pytest.mark.parametrize(("api_key", "want"), [("", False), ("k", True)])
def test_usable_chat_cfg_needs_a_key(api_key, want):
    cfg = {"provider": "deepseek", "api_key": api_key, "base_url": _OWN_HOST}
    assert chain_generator._is_usable_openai_chat_cfg(cfg) is want


class _AsyncRecorder:
    def __init__(self, answer):
        self.calls: list = []
        self.answer = answer

    async def __call__(self, *args, **kwargs):
        self.calls.append(args)
        return self.answer


@pytest.mark.parametrize("provider", ["deepseek", "cursor"])
def test_chat_in_mock_mode_raises_before_any_call(env, monkeypatch, provider):
    env(LLM_PROVIDER=provider, CEREBRUM_CHAT_LLM_API_KEY=_key(), CEREBRUM_LLM_MOCK="1")
    rec = _AsyncRecorder({"message": "called"})
    monkeypatch.setattr(chain_generator, "_call_openai_compatible", rec)
    monkeypatch.setattr(chain_generator, "_call_openrouter_fallback", _AsyncRecorder(None))
    with pytest.raises(RuntimeError, match="mock"):
        asyncio.run(chain_generator._call_llm([{"role": "user", "content": "u"}]))
    assert rec.calls == []


def test_chat_with_nothing_configured_raises_not_configured(env, monkeypatch):
    monkeypatch.setattr(chain_generator, "_call_openrouter_fallback", _AsyncRecorder(None))
    with pytest.raises(RuntimeError, match="No LLM provider configured"):
        asyncio.run(chain_generator._call_llm([{"role": "user", "content": "u"}]))


@pytest.mark.parametrize("exc", [RuntimeError("a"), ValueError("b")])
def test_soft_failure_is_an_answer_not_a_raise(exc):
    out = chain_generator._soft_failure_response(exc)
    assert (out["chain"], out["rules"], bool(out["message"])) == (None, [], True)


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self._content = content

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"choices": [{"message": {"content": self._content}}]}


class _FakeAsyncClient:
    posts: list = []

    def __init__(self, *a, **k) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, headers=None):
        _FakeAsyncClient.posts.append({"url": url, "json": json, "headers": headers})
        return _FakeResponse('{"answer": "' + json["model"] + '"}')


@pytest.mark.parametrize(
    ("base", "pinned"),
    [(_OWN_HOST, True), (_OPENROUTER, False)],
)
def test_openai_compatible_call_pins_json_only_off_openrouter(monkeypatch, base, pinned):
    _FakeAsyncClient.posts = []
    monkeypatch.setattr(chain_generator.httpx, "AsyncClient", _FakeAsyncClient)
    out = asyncio.run(
        chain_generator._call_openai_compatible(
            base, "k", "m-1", [{"role": "user", "content": "u"}]
        )
    )
    assert out == {"answer": "m-1"}
    assert ("response_format" in _FakeAsyncClient.posts[0]["json"]) is pinned
    assert _FakeAsyncClient.posts[0]["url"] == f"{base}/chat/completions"


@pytest.mark.parametrize("base", [_CURSOR, _CURSOR + "/"])
def test_openai_compatible_call_refuses_the_cursor_host(monkeypatch, base):
    _FakeAsyncClient.posts = []
    monkeypatch.setattr(chain_generator.httpx, "AsyncClient", _FakeAsyncClient)
    with pytest.raises(RuntimeError, match="Refusing"):
        asyncio.run(
            chain_generator._call_openai_compatible(
                base, "k", "m-1", [{"role": "user", "content": "u"}]
            )
        )
    assert _FakeAsyncClient.posts == []


# -- rule parser ----------------------------------------------------------------


class _FakeClient:
    posts: list = []

    def __init__(self, *a, **k) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, json=None, headers=None):
        _FakeClient.posts.append({"url": url, "json": json})
        return _FakeResponse('{"rules": ["' + json["model"] + '"]}')


@pytest.mark.parametrize(("raw", "want"), [("0.4", 0.4), ("", None)])
def test_rule_parser_call_answers_and_sends_temperature_only_when_set(env, monkeypatch, raw, want):
    _FakeClient.posts = []
    env(LLM_TEMPERATURE=raw)
    monkeypatch.setattr(rule_parser.httpx, "Client", _FakeClient)
    out = rule_parser._call_openai_sync(_OWN_HOST, "k", "m-2", [{"role": "user", "content": "u"}])
    assert out == {"rules": ["m-2"]}
    assert _FakeClient.posts[0]["json"].get("temperature") == want


# -- architect (product_architect) ----------------------------------------------


@pytest.mark.parametrize("use_chat_config", [True, False])
def test_architect_in_mock_mode_raises_before_any_call(env, monkeypatch, use_chat_config):
    from app.factory import llm_watchdog

    env(
        LLM_PROVIDER="deepseek",
        CEREBRUM_CHAT_LLM_API_KEY=_key(),
        CEREBRUM_FACTORY_LLM_API_KEY=_key(),
        CEREBRUM_LLM_MOCK="1",
    )
    calls: list = []
    monkeypatch.setattr(
        llm_watchdog, "post_with_deadline", lambda *a, **k: calls.append(a) or _FakeResponse("{}")
    )
    with pytest.raises(RuntimeError, match="mock"):
        product_architect._llm_json_call(
            [{"role": "user", "content": "u"}], use_chat_config=use_chat_config
        )
    assert calls == []


# -- app start: the backup task is cancelled on the way out ---------------------


class _Task:
    def __init__(self) -> None:
        self.cancelled = 0

    def cancel(self) -> None:
        self.cancelled += 1


@pytest.mark.parametrize(("task", "want"), [(_Task, 1), (None, None)])
def test_lifespan_cancels_the_backup_task_it_started(env, monkeypatch, task, want):
    from app import main
    from app.cerebrum_product_kernel.ingestion import engine_install
    from app.factory.build import agent_process, orphan_recovery

    started = task() if task else None
    monkeypatch.setattr(main.backup_scheduler, "start", lambda: started)
    monkeypatch.setattr(engine_install, "install_default_engine", lambda: None)
    monkeypatch.setattr(agent_process, "guard_exit_signals", lambda: None)
    monkeypatch.setattr(orphan_recovery, "recover_orphaned_model_calls", lambda: None)
    monkeypatch.setattr(orphan_recovery, "recover_stranded_n3_handoffs", lambda: None)

    async def _run():
        async with main._lifespan(main.app):
            pass

    asyncio.run(_run())
    assert (started.cancelled if started else None) == want


# -- cursor HTTP config: a keyless resolver still takes the chat key -----------


@pytest.mark.parametrize(("chat_key", "want_error"), [("k-chat", False), ("", True)])
def test_cursor_http_config_takes_the_chat_key_when_the_resolvers_return_none(
    env, monkeypatch, chat_key, want_error
):
    # Through settings alone a present chat key is always picked by the scoped
    # resolver first; this pins the branch's own contract with the resolvers
    # answering keyless, so the branch cannot silently stop arming the config.
    monkeypatch.setattr(llm_config, "_scoped_path_endpoint", lambda *p: None)
    monkeypatch.setattr(llm_config, "_resolve_shared_primary", lambda *p: {"base_url": _OWN_HOST})
    if chat_key:
        env(CEREBRUM_CHAT_LLM_API_KEY=chat_key)
    cfg = llm_config._cursor_http_config()
    assert (cfg["api_key"], "error" in cfg) == (chat_key, want_error)


# -- coder: wire shape per leg, empty answers, one attempt deadline ------------


class _CoderResp:
    status_code = 200

    def __init__(self, body: dict) -> None:
        self._body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._body


class _CoderPost:
    def __init__(self, body: dict) -> None:
        self.body = body
        self.calls: list[dict] = []

    def __call__(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return _CoderResp(self.body)


_BOTH_SHAPES = {
    "choices": [{"message": {"content": "code-out"}, "finish_reason": "stop"}],
    "content": [{"type": "text", "text": "code-out"}],
}


@pytest.mark.parametrize(
    ("provider", "key_env", "messages_api"),
    [("claude", "ANTHROPIC_API_KEY", True), ("deepseek", "CEREBRUM_FACTORY_LLM_API_KEY", False)],
)
def test_coder_sends_each_leg_in_its_own_wire_shape(
    env, monkeypatch, provider, key_env, messages_api
):
    from app.factory import coder

    env(LLM_PROVIDER=provider, **{key_env: _key()})
    post = _CoderPost(_BOTH_SHAPES)
    monkeypatch.setattr(coder, "post_with_deadline", post)
    text, _model = coder._llm_code_call_impl([{"role": "user", "content": "u"}])
    sent = post.calls[0]
    assert text == "code-out"
    assert sent["url"].endswith("/chat/completions") is not messages_api
    assert ("x-api-key" in sent["headers"]) is messages_api
    assert ("Authorization" in sent["headers"]) is not messages_api


@pytest.mark.parametrize("content", ["", "   "])
def test_coder_refuses_an_empty_completion(env, monkeypatch, content):
    from app.factory import coder

    env(LLM_PROVIDER="deepseek", CEREBRUM_FACTORY_LLM_API_KEY=_key())
    body = {"choices": [{"message": {"content": content}, "finish_reason": "length"}]}
    monkeypatch.setattr(coder, "post_with_deadline", _CoderPost(body))
    with pytest.raises(coder.CoderError, match="empty completion"):
        coder._llm_code_call_impl([{"role": "user", "content": "u"}])


@pytest.mark.parametrize("preset", [None, 12345.0])
def test_coder_call_holds_one_attempt_deadline_and_clears_its_own(monkeypatch, preset):
    from app.factory import coder

    seen: list = []
    monkeypatch.setattr(
        coder,
        "_llm_code_call_impl",
        lambda messages: seen.append(coder._attempt_deadline.get()) or ("t", "m"),
    )
    token = coder._attempt_deadline.set(preset) if preset is not None else None
    try:
        assert coder._llm_code_call([{"role": "user", "content": "u"}]) == ("t", "m")
        assert coder._attempt_deadline.get() == preset
    finally:
        if token is not None:
            coder._attempt_deadline.reset(token)
    assert isinstance(seen[0], float)
    assert seen[0] == preset if preset is not None else seen[0] > 0
