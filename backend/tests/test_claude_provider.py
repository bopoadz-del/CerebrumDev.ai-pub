"""Claude is an addition, not a swap: Kimi must stay the default.

New-shape tests for second-provider support. The failure that matters here is
not a crash -- it is a *silent* one. Two shapes specifically:

* Adding an ANTHROPIC_API_KEY to a working Kimi deployment must not move that
  deployment's traffic, or its bill, onto Claude. A cost surprise is a product
  bug, so "both keys present" resolving to kimi is asserted directly.
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
    "KIMI_API_KEY",
    "KIMI_BASE_URL",
    "KIMI_MODEL",
    "KIMI_MOCK",
    "CLAUDE_MOCK",
    "CEREBRUM_LLM_MOCK",
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
FAKE_KIMI = "sk-kimi-not-a-real-key"
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
    os.environ["KIMI_API_KEY"] = FAKE_KIMI
    assert get_llm_config()["provider"] == "kimi"


def test_claude_only_resolves_to_claude():
    os.environ["ANTHROPIC_API_KEY"] = FAKE_CLAUDE
    cfg = get_llm_config()
    assert cfg["provider"] == "claude"
    assert cfg["base_url"] == "https://api.anthropic.com/v1"


def test_both_present_resolves_to_kimi():
    """The load-bearing one: a second key must not move anyone's bill."""
    os.environ["KIMI_API_KEY"] = FAKE_KIMI
    os.environ["ANTHROPIC_API_KEY"] = FAKE_CLAUDE
    assert get_llm_config()["provider"] == "kimi"
    assert get_factory_llm_config()["provider"] == "kimi"


def test_neither_present_resolves_to_no_provider():
    assert get_llm_config()["provider"] == ""


def test_claude_is_used_when_asked_for_even_though_kimi_is_configured():
    os.environ["KIMI_API_KEY"] = FAKE_KIMI
    os.environ["ANTHROPIC_API_KEY"] = FAKE_CLAUDE
    os.environ["LLM_PROVIDER"] = "claude"
    assert get_llm_config()["provider"] == "claude"
    assert get_factory_llm_config()["provider"] == "claude"


@pytest.mark.parametrize(
    ("raw", "expected"),
    (
        ("moonshot", "kimi"),
        ("kimi", "kimi"),
        ("anthropic", "claude"),
        ("claude", "claude"),
        ("Anthropic", "claude"),
        ("qwen", "qwen"),
    ),
)
def test_provider_aliases(raw, expected):
    assert normalise_provider(raw) == expected


def test_anthropic_alias_reaches_the_claude_config():
    os.environ["LLM_PROVIDER"] = "anthropic"
    os.environ["ANTHROPIC_API_KEY"] = FAKE_CLAUDE
    assert get_llm_config()["provider"] == "claude"


# -- fail closed, both directions ----------------------------------------


def test_claude_without_a_key_errors_and_does_not_borrow_kimi():
    os.environ["LLM_PROVIDER"] = "claude"
    os.environ["KIMI_API_KEY"] = FAKE_KIMI  # present, and must NOT be used
    cfg = get_factory_llm_config()
    assert cfg["provider"] == "claude"
    assert cfg["api_key"] == ""
    assert "ANTHROPIC_API_KEY" in cfg.get("error", "")
    assert FAKE_KIMI not in str(cfg)


def test_kimi_without_a_key_errors_and_does_not_borrow_claude():
    os.environ["LLM_PROVIDER"] = "kimi"
    os.environ["ANTHROPIC_API_KEY"] = FAKE_CLAUDE  # present, must NOT be used
    cfg = get_factory_llm_config()
    assert cfg["provider"] == "kimi"
    assert cfg["api_key"] == ""
    assert "KIMI_API_KEY" in cfg.get("error", "")
    assert FAKE_CLAUDE not in str(cfg)


def test_the_coder_refuses_rather_than_switching_provider(monkeypatch):
    from app.factory.coder import CoderError, _llm_code_call

    monkeypatch.setenv("LLM_PROVIDER", "claude")
    monkeypatch.setenv("KIMI_API_KEY", FAKE_KIMI)
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

    monkeypatch.setenv("LLM_PROVIDER", "kimi")
    monkeypatch.setenv("KIMI_API_KEY", FAKE_KIMI)
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
    assert sent["headers"]["Authorization"] == f"Bearer {FAKE_KIMI}"
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
    assert set(SUPPORTED_PROVIDERS) == {"deepseek", "cursor", "kimi", "claude"}
