"""Claude is an addition, not a swap: it never takes another vendor's traffic.

New-shape tests for second-provider support. The failure that matters here is
not a crash -- it is a *silent* one. Two shapes specifically:

* Adding an ANTHROPIC_API_KEY to a working deployment must not move that
  deployment's traffic, or its bill, onto Claude. A cost surprise is a product
  bug, so "both keys present, no LLM_PROVIDER" is an explicit error (WK-2).
* Asking for a provider whose key is missing must be a loud error, never a
  quiet fall-through to whichever provider happens to be configured.

The Anthropic call is asserted by request shape rather than by hitting the
network. Its contract differs from OpenAI's in three ways that a port
silently gets wrong -- x-api-key instead of a bearer token, a mandatory
anthropic-version header, and the system prompt as a top-level parameter
rather than a message role.
"""

from __future__ import annotations

import os

import pytest

from app.core.llm_config import (
    SUPPORTED_PROVIDERS,
    get_factory_llm_config,
    get_llm_config,
    normalise_provider,
)

# Fake credentials for this test, generated per run (no value is stored in the repo).
_GENERATED_KEY_1 = "sk-" + __import__("secrets").token_hex(11)

CRED_VARS = [
    "LLM_PROVIDER",
    "CEREBRUM_LLM_API_KEY",
    "CEREBRUM_LLM_BASE_URL",
    "CEREBRUM_LLM_MODEL",
    "DEEPSEEK_API_KEY",
    "KIMI_BASE_URL",
    "KIMI_MODEL",
    "CEREBRUM_LLM_MOCK",
    "CLAUDE_MOCK",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_MODEL",
    "CLAUDE_API_KEY",
    "CLAUDE_MODEL",
    "CEREBRUM_CHAT_LLM_API_KEY",
    "CEREBRUM_FACTORY_LLM_API_KEY",
    "CEREBRUM_FACTORY_LLM_MODEL",
    "CURSOR_API_KEY",
    "CURSOR_AGENT_API_KEY",
    "FACTORY_CURSOR_API_KEY",
]

# Obviously-fake values. No real key may appear in this repository.
FAKE_PRIMARY = "sk-primary-not-a-real-key"
FAKE_CLAUDE = "sk-ant-not-a-real-key"


@pytest.fixture(autouse=True)
def _clean_env():
    old = {k: os.environ.get(k) for k in CRED_VARS}
    for k in CRED_VARS:
        os.environ.pop(k, None)
    yield
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


# -- the four credential combinations ------------------------------------


def test_kimi_only_resolves_to_kimi():
    """WK-2: LLM_PROVIDER unset with ONE vendor's key resolves to that vendor
    (the shared slot is the deepseek row)."""
    os.environ["CEREBRUM_LLM_API_KEY"] = FAKE_PRIMARY
    assert get_llm_config()["provider"] == "deepseek"
    assert get_factory_llm_config()["provider"] == "deepseek"


def test_claude_only_resolves_to_claude():
    os.environ["ANTHROPIC_API_KEY"] = FAKE_CLAUDE
    cfg = get_llm_config()
    assert cfg["provider"] == "claude"
    assert cfg["base_url"] == "https://api.anthropic.com/v1"


def test_both_present_resolves_to_kimi():
    """The load-bearing one: a second key must not move anyone's bill.

    WK-2: with no LLM_PROVIDER, two vendors' keys are AMBIGUOUS -- an error
    naming the setting to set, no endpoint resolved, and marked as a config
    error so no fallback leg can hide it.
    """
    os.environ["CEREBRUM_LLM_API_KEY"] = FAKE_PRIMARY
    os.environ["ANTHROPIC_API_KEY"] = FAKE_CLAUDE
    for cfg in (get_llm_config(), get_factory_llm_config()):
        assert cfg["provider"] == ""
        assert cfg["api_key"] == ""
        assert "more than one provider" in cfg["error"]
        assert "LLM_PROVIDER" in cfg["error"]
        assert cfg["config_error"] is True
        assert FAKE_PRIMARY not in str(cfg) and FAKE_CLAUDE not in str(cfg)


def test_neither_present_resolves_to_no_provider():
    assert get_llm_config()["provider"] == ""


def test_claude_is_used_when_asked_for_even_though_kimi_is_configured():
    os.environ["DEEPSEEK_API_KEY"] = FAKE_PRIMARY
    os.environ["ANTHROPIC_API_KEY"] = FAKE_CLAUDE
    os.environ["LLM_PROVIDER"] = "claude"
    assert get_llm_config()["provider"] == "claude"
    assert get_factory_llm_config()["provider"] == "claude"


@pytest.mark.parametrize(
    ("raw", "expected"),
    (
        ("moonshot", "moonshot"),
        ("kimi", "kimi"),
        ("anthropic", "claude"),
        ("claude", "claude"),
        ("Anthropic", "claude"),
        ("qwen", "qwen"),
    ),
)
def test_provider_aliases(raw, expected):
    """``anthropic`` is the one alias. Retired names are NOT aliases: they
    pass through unchanged and the table then refuses them."""
    assert normalise_provider(raw) == expected
    if expected not in SUPPORTED_PROVIDERS:
        os.environ["LLM_PROVIDER"] = raw
        assert get_llm_config()["provider"] == ""
        assert "not in the provider table" in get_llm_config()["error"]


def test_anthropic_alias_reaches_the_claude_config():
    os.environ["LLM_PROVIDER"] = "anthropic"
    os.environ["ANTHROPIC_API_KEY"] = FAKE_CLAUDE
    assert get_llm_config()["provider"] == "claude"


# -- fail closed, both directions ----------------------------------------


def test_claude_without_a_key_errors_and_does_not_borrow_kimi():
    os.environ["LLM_PROVIDER"] = "claude"
    os.environ["DEEPSEEK_API_KEY"] = FAKE_PRIMARY  # present, and must NOT be used
    cfg = get_factory_llm_config()
    assert cfg["provider"] == "claude"
    assert cfg["api_key"] == ""
    assert "ANTHROPIC_API_KEY" in cfg.get("error", "")
    assert FAKE_PRIMARY not in str(cfg)


def test_kimi_without_a_key_errors_and_does_not_borrow_claude():
    """A provider named without its key is an error; the other vendor's key,
    present, is never borrowed."""
    os.environ["LLM_PROVIDER"] = "deepseek"
    os.environ["ANTHROPIC_API_KEY"] = FAKE_CLAUDE  # present, must NOT be used
    cfg = get_factory_llm_config()
    assert cfg["provider"] == "deepseek"
    assert cfg["api_key"] == ""
    assert "deepseek selected but no key is set" in cfg.get("error", "")
    assert FAKE_CLAUDE not in str(cfg)


def test_the_coder_refuses_rather_than_switching_provider(monkeypatch):
    from app.factory.coder import CoderError, _llm_code_call

    monkeypatch.setenv("LLM_PROVIDER", "claude")
    monkeypatch.setenv("DEEPSEEK_API_KEY", FAKE_PRIMARY)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(CoderError, match="ANTHROPIC_API_KEY"):
        _llm_code_call([{"role": "user", "content": "hi"}])


# -- the Anthropic wire contract -----------------------------------------


def test_anthropic_request_shape_is_not_openai_shaped():
    from app.factory.coder import ANTHROPIC_VERSION, _anthropic_request

    cfg = {
        "api_key": FAKE_CLAUDE,
        "base_url": "https://api.anthropic.com/v1",
        "provider": "claude",
    }
    url, payload, headers = _anthropic_request(
        cfg,
        [
            {"role": "system", "content": "SYS-ONE"},
            {"role": "user", "content": "hello"},
        ],
        "claude-sonnet-4-5",
    )

    assert url == "https://api.anthropic.com/v1/messages"
    # x-api-key, not Authorization: Bearer.
    assert headers["x-api-key"] == FAKE_CLAUDE
    assert "Authorization" not in headers
    assert headers["anthropic-version"] == ANTHROPIC_VERSION
    # System prompt is top-level, and is NOT left in messages.
    assert payload["system"] == "SYS-ONE"
    assert all(m["role"] != "system" for m in payload["messages"])
    assert payload["messages"] == [{"role": "user", "content": "hello"}]
    # max_tokens is required by the Messages API.
    assert payload["max_tokens"] > 0
    assert payload["model"] == "claude-sonnet-4-5"


def test_multiple_system_messages_are_merged_into_the_system_param():
    from app.factory.coder import _anthropic_request

    _url, payload, _headers = _anthropic_request(
        {"api_key": FAKE_CLAUDE, "base_url": "https://x/v1"},
        [
            {"role": "system", "content": "A"},
            {"role": "system", "content": "B"},
            {"role": "user", "content": "go"},
        ],
        "m",
    )
    assert payload["system"] == "A\n\nB"


def test_content_blocks_are_parsed_and_non_text_blocks_ignored():
    from app.factory.coder import _anthropic_text

    data = {
        "content": [
            {"type": "thinking", "thinking": "ignore me"},
            {"type": "text", "text": "return {"},
            {"type": "text", "text": '"ok": True}'},
        ]
    }
    assert _anthropic_text(data) == 'return {"ok": True}'


def test_an_all_non_text_response_is_an_error_not_an_empty_body():
    from app.factory.coder import _anthropic_text

    with pytest.raises(ValueError, match="empty completion"):
        _anthropic_text({"content": [{"type": "tool_use", "id": "t"}]})


def test_claude_code_call_posts_the_messages_endpoint(monkeypatch):
    """End of the wire, still no network: assert what would have been sent."""
    import app.factory.coder as coder

    monkeypatch.setenv("LLM_PROVIDER", "claude")
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_CLAUDE)
    sent = {}

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"content": [{"type": "text", "text": "    return {}"}]}

    def fake_post(url, json=None, headers=None, timeout=None):
        sent.update(url=url, json=json, headers=headers)
        return _Resp()

    monkeypatch.setattr(coder.httpx, "post", fake_post)

    out, model_used = coder._llm_code_call(
        [{"role": "system", "content": "S"}, {"role": "user", "content": "U"}]
    )
    assert out == "    return {}"
    assert model_used == sent["json"]["model"], "reported a model it did not call"
    assert sent["url"].endswith("/messages")
    assert "chat/completions" not in sent["url"]
    assert sent["headers"]["x-api-key"] == FAKE_CLAUDE
    assert sent["json"]["system"] == "S"


def test_kimi_code_call_is_unchanged_by_claude_support(monkeypatch):
    """Regression guard on the default path's wire format."""
    import app.factory.coder as coder

    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("CEREBRUM_FACTORY_LLM_API_KEY", FAKE_PRIMARY)
    sent = {}

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "    return {}"}}]}

    def fake_post(url, json=None, headers=None, timeout=None):
        sent.update(url=url, json=json, headers=headers)
        return _Resp()

    monkeypatch.setattr(coder.httpx, "post", fake_post)

    assert coder._llm_code_call([{"role": "user", "content": "U"}])[0] == "    return {}"
    assert sent["url"].endswith("/chat/completions")
    assert sent["headers"]["Authorization"] == f"Bearer {FAKE_PRIMARY}"
    assert "x-api-key" not in sent["headers"]


# -- the agentic CLI seam -------------------------------------------------


def test_code_cli_prefers_the_provider_agnostic_name(monkeypatch):
    from app.factory.coder import code_cli_command

    monkeypatch.delenv("FACTORY_CODE_CLI", raising=False)
    monkeypatch.delenv("KIMI_CODE_CLI", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("FACTORY_CODE_PROVIDER", raising=False)
    assert code_cli_command() == "kimi"

    # Backwards compatibility: existing deployments keep working untouched.
    monkeypatch.setenv("KIMI_CODE_CLI", "/opt/kimi")
    assert code_cli_command() == "/opt/kimi"

    # And the new name wins when both are set.
    monkeypatch.setenv("FACTORY_CODE_CLI", "/usr/bin/claude")
    assert code_cli_command() == "/usr/bin/claude"


def test_code_cli_defaults_to_kimi_when_deepseek_key_present(monkeypatch):
    from app.factory.coder import code_cli_command, factory_code_provider

    monkeypatch.delenv("FACTORY_CODE_CLI", raising=False)
    monkeypatch.delenv("KIMI_CODE_CLI", raising=False)
    monkeypatch.delenv("FACTORY_CODE_PROVIDER", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", _GENERATED_KEY_1)
    assert factory_code_provider() == "deepseek"
    assert code_cli_command() == "kimi"
    monkeypatch.setenv("FACTORY_CODE_CLI", "kimi")
    assert code_cli_command() == "kimi"
    assert factory_code_provider() == "deepseek"
    monkeypatch.setenv("FACTORY_CODE_PROVIDER", "kimi")
    assert factory_code_provider() == "kimi"


def test_supported_providers_include_cursor():
    assert set(SUPPORTED_PROVIDERS) == {"deepseek", "cursor", "claude"}
