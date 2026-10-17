"""Tests for unified LLM provider configuration."""

import os
import pytest

from app.core.llm_config import (
    _chat_http_host,
    _redact_ready_secrets,
    active_provider,
    get_factory_llm_config,
    get_llm_config,
    llm_ready_details,
)

# Fake credentials for this test, generated per run (no value is stored in the repo).
_GENERATED_KEY_1 = "sk-" + __import__("secrets").token_hex(11)


@pytest.fixture(autouse=True)
def _clear_env():
    keys = [
        "LLM_PROVIDER",
        "CEREBRUM_LLM_API_KEY",
        "CEREBRUM_LLM_BASE_URL",
        "CEREBRUM_LLM_MODEL",
        "KIMI_API_KEY",
        "KIMI_BASE_URL",
        "KIMI_MODEL",
        "KIMI_MOCK",
        "CEREBRUM_LLM_MOCK",
        "CEREBRUM_CHAT_LLM_API_KEY",
        "CEREBRUM_CHAT_LLM_BASE_URL",
        "CEREBRUM_CHAT_LLM_MODEL",
        "CEREBRUM_FACTORY_LLM_API_KEY",
        "CEREBRUM_FACTORY_LLM_BASE_URL",
        "CEREBRUM_FACTORY_LLM_MODEL",
        "OPENROUTER_API_KEY",
        "FACTORY_LLM_FALLBACK_API_KEY",
        "DEEPSEEK_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "CURSOR_API_KEY",
        "CURSOR_AGENT_API_KEY",
        "FACTORY_CURSOR_API_KEY",
        "OPENROUTER_MODEL",
        "FACTORY_LLM_FALLBACK_MODEL",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_MODEL",
        "CURSOR_API_KEY",
        "CURSOR_AGENT_API_KEY",
        "FACTORY_CURSOR_API_KEY",
        "CURSOR_BASE_URL",
        "CURSOR_LLM_BASE_URL",
        "CURSOR_MODEL",
        "CURSOR_MOCK",
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


def test_explicit_moonshot_aliases_to_kimi():
    os.environ["LLM_PROVIDER"] = "moonshot"
    os.environ["CEREBRUM_LLM_API_KEY"] = "sk-moon"
    os.environ["CEREBRUM_LLM_MODEL"] = "moonshot-v1-32k"
    cfg = get_llm_config()
    assert cfg["provider"] == "kimi"
    assert cfg["api_key"] == "sk-moon"
    assert cfg["model"] == "moonshot-v1-32k"


def test_explicit_kimi():
    os.environ["LLM_PROVIDER"] = "kimi"
    os.environ["KIMI_API_KEY"] = "sk-kimi"
    os.environ["KIMI_MODEL"] = "kimi-k2"
    cfg = get_llm_config()
    assert cfg["provider"] == "kimi"
    assert cfg["api_key"] == "sk-kimi"
    assert cfg["model"] == "kimi-k2"


def test_factory_llm_rejects_an_unsupported_provider():
    """Behaviour unchanged; only the message names the supported set.

    Was `test_factory_llm_kimi_only_rejects_qwen`, asserting the error said
    "Kimi-only". Claude support makes that string false, so the assertion moved
    to what actually matters: an unknown provider is still refused, still with
    an empty provider and an error, and the message says what IS allowed.
    """
    os.environ["LLM_PROVIDER"] = "qwen"
    cfg = get_factory_llm_config()
    assert cfg["provider"] == ""
    error = cfg.get("error", "")
    assert "qwen" in error
    assert "cursor" in error and "kimi" in error and "claude" in error


def test_factory_llm_mock():
    os.environ["KIMI_MOCK"] = "1"
    cfg = get_factory_llm_config()
    assert cfg["provider"] == "kimi"
    assert cfg["mock"] is True


def test_kimi_mock_does_not_activate_live_kit_provider():
    """Kit chat must stay offline when only mock flags are set (no API key)."""
    os.environ["KIMI_MOCK"] = "1"
    cfg = get_llm_config()
    assert cfg["provider"] == ""
    assert cfg["mock"] is True
    assert active_provider() == ""


def test_no_provider():
    cfg = get_llm_config()
    assert cfg["provider"] == ""


def test_chat_and_factory_use_separate_models():
    """Chat and Factory architect can point at different Kimi models."""
    os.environ["CEREBRUM_LLM_API_KEY"] = "sk-shared"
    os.environ["CEREBRUM_CHAT_LLM_MODEL"] = "kimi-k2.7"
    os.environ["CEREBRUM_FACTORY_LLM_MODEL"] = "kimi-k2.7-code"
    chat_cfg = get_llm_config()
    factory_cfg = get_factory_llm_config()
    assert chat_cfg["model"] == "kimi-k2.7"
    assert factory_cfg["model"] == "kimi-k2.7-code"
    assert chat_cfg["provider"] == "kimi"
    assert factory_cfg["provider"] == "kimi"


def test_chat_and_factory_fallback_to_shared_vars():
    """When scoped vars are absent, both paths fall back to CEREBRUM_LLM_*."""
    os.environ["CEREBRUM_LLM_API_KEY"] = "sk-shared"
    os.environ["CEREBRUM_LLM_MODEL"] = "moonshot-v1-8k"
    chat_cfg = get_llm_config()
    factory_cfg = get_factory_llm_config()
    assert chat_cfg["model"] == "moonshot-v1-8k"
    assert factory_cfg["model"] == "moonshot-v1-8k"


def test_openrouter_base_does_not_steal_primary_when_moonshot_key_exists():
    """A leftover OpenRouter base_url must not hijack Floor chat.

    Live failure: CEREBRUM_LLM_API_KEY (Kimi) was healthy, /ready said
    llm_configured, but CEREBRUM_LLM_BASE_URL=openrouter.ai sent suggestions
    through a 401 OPENROUTER_API_KEY.
    """
    os.environ["CEREBRUM_LLM_BASE_URL"] = "https://openrouter.ai/api/v1"
    os.environ["CEREBRUM_LLM_MODEL"] = "minimax/minimax-m3:free"
    os.environ["OPENROUTER_API_KEY"] = "sk-or-test"
    os.environ["CEREBRUM_LLM_API_KEY"] = "sk-moonshot-shared"
    os.environ["KIMI_API_KEY"] = "sk-moonshot-kimi"

    chat_cfg = get_llm_config()
    factory_cfg = get_factory_llm_config()

    assert chat_cfg["provider"] == "kimi"
    assert factory_cfg["provider"] == "kimi"
    assert chat_cfg["base_url"] == "https://api.moonshot.ai/v1"
    assert factory_cfg["base_url"] == "https://api.moonshot.ai/v1"
    assert chat_cfg["api_key"] == "sk-moonshot-kimi"
    assert factory_cfg["api_key"] == "sk-moonshot-kimi"
    assert "openrouter" not in chat_cfg["model"]
    assert "/" not in chat_cfg["model"]
    assert "error" not in factory_cfg


def test_cerebrum_chat_scoped_key_wins_over_leftover_kimi():
    """CEREBRUM_CHAT_LLM_* is Floor chat even when leftover KIMI_* remains."""
    os.environ["CEREBRUM_CHAT_LLM_BASE_URL"] = "https://OpenRouter.AI/api/v1"
    os.environ["CEREBRUM_CHAT_LLM_API_KEY"] = "sk-or-chat-override"
    os.environ["CEREBRUM_CHAT_LLM_MODEL"] = "minimax/minimax-m3:free"
    os.environ["KIMI_API_KEY"] = "sk-moonshot-kimi"

    chat_cfg = get_llm_config()
    assert chat_cfg["api_key"] == "sk-or-chat-override"
    assert chat_cfg["base_url"] == "https://OpenRouter.AI/api/v1"
    assert chat_cfg["model"] == "minimax/minimax-m3:free"
    assert "api.cursor.com" not in chat_cfg["base_url"]


def test_moonshot_base_still_uses_kimi_keys_when_openrouter_key_present():
    """Default Moonshot host must keep using Moonshot credentials."""
    os.environ["KIMI_API_KEY"] = "sk-moonshot-kimi"
    os.environ["CEREBRUM_LLM_API_KEY"] = "sk-moonshot-shared"
    os.environ["OPENROUTER_API_KEY"] = "sk-or-test"

    chat_cfg = get_llm_config()
    factory_cfg = get_factory_llm_config()

    assert chat_cfg["provider"] == "kimi"
    assert factory_cfg["provider"] == "kimi"
    assert chat_cfg["api_key"] == "sk-moonshot-kimi"
    assert factory_cfg["api_key"] == "sk-moonshot-kimi"
    assert chat_cfg["base_url"] == "https://api.moonshot.ai/v1"
    assert factory_cfg["base_url"] == "https://api.moonshot.ai/v1"


def test_openrouter_base_without_openrouter_key_uses_moonshot_primary():
    """Moonshot credentials stay on Moonshot even when the base_url is OpenRouter."""
    os.environ["CEREBRUM_LLM_BASE_URL"] = "https://openrouter.ai/api/v1"
    os.environ["KIMI_API_KEY"] = "sk-moonshot-kimi"
    os.environ["CEREBRUM_LLM_API_KEY"] = "sk-moonshot-shared"

    chat_cfg = get_llm_config()
    factory_cfg = get_factory_llm_config()

    assert chat_cfg["api_key"] == "sk-moonshot-kimi"
    assert factory_cfg["api_key"] == "sk-moonshot-kimi"
    assert chat_cfg["base_url"] == "https://api.moonshot.ai/v1"
    assert factory_cfg["base_url"] == "https://api.moonshot.ai/v1"
    assert "error" not in factory_cfg


def test_openrouter_key_alone_activates_chat_when_base_is_openrouter():
    """Floor chat must not require a Moonshot key just to talk to OpenRouter."""
    os.environ["CEREBRUM_LLM_BASE_URL"] = "https://openrouter.ai/api/v1"
    os.environ["OPENROUTER_API_KEY"] = "sk-or-test"

    chat_cfg = get_llm_config()
    factory_cfg = get_factory_llm_config()

    assert chat_cfg["provider"] == "kimi"
    assert factory_cfg["provider"] == "kimi"
    assert chat_cfg["api_key"] == "sk-or-test"
    assert factory_cfg["api_key"] == "sk-or-test"


def test_openrouter_only_uses_factory_fallback_key_when_no_moonshot_key():
    """OpenRouter remains a valid primary only when no Moonshot key exists."""
    os.environ["CEREBRUM_LLM_BASE_URL"] = "https://openrouter.ai/api/v1"
    os.environ["FACTORY_LLM_FALLBACK_API_KEY"] = "sk-or-fallback"

    chat_cfg = get_llm_config()
    factory_cfg = get_factory_llm_config()

    assert chat_cfg["api_key"] == "sk-or-fallback"
    assert factory_cfg["api_key"] == "sk-or-fallback"
    assert chat_cfg["base_url"] == "https://openrouter.ai/api/v1"


def test_moonshot_key_plus_fallback_key_keeps_openrouter_off_primary():
    os.environ["CEREBRUM_LLM_BASE_URL"] = "https://openrouter.ai/api/v1"
    os.environ["FACTORY_LLM_FALLBACK_API_KEY"] = "sk-or-fallback"
    os.environ["KIMI_API_KEY"] = "sk-moonshot-kimi"

    chat_cfg = get_llm_config()
    factory_cfg = get_factory_llm_config()

    assert chat_cfg["api_key"] == "sk-moonshot-kimi"
    assert factory_cfg["api_key"] == "sk-moonshot-kimi"
    assert chat_cfg["base_url"] == "https://api.moonshot.ai/v1"


def test_claude_primary_skips_openrouter_base_when_anthropic_key_exists():
    os.environ["LLM_PROVIDER"] = "claude"
    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test"
    os.environ["CEREBRUM_LLM_BASE_URL"] = "https://openrouter.ai/api/v1"
    os.environ["OPENROUTER_API_KEY"] = "sk-or-test"

    chat_cfg = get_llm_config()
    assert chat_cfg["provider"] == "claude"
    assert chat_cfg["api_key"] == "sk-ant-test"
    assert chat_cfg["base_url"] == "https://api.anthropic.com/v1"


def test_explicit_cursor_uses_cerebrum_chat_not_cursor_host():
    """LLM_PROVIDER=cursor names BA; HTTP chat uses CEREBRUM_CHAT_LLM_*."""
    os.environ["LLM_PROVIDER"] = "cursor"
    os.environ["CURSOR_API_KEY"] = "crsr-test-not-real"
    os.environ["CEREBRUM_CHAT_LLM_API_KEY"] = "chat-key-live"
    os.environ["CEREBRUM_CHAT_LLM_BASE_URL"] = "https://chat.example.test/v1"
    os.environ["CEREBRUM_CHAT_LLM_MODEL"] = "chat-model-live"

    chat_cfg = get_llm_config()
    factory_cfg = get_factory_llm_config()

    assert chat_cfg["provider"] == "cursor"
    assert factory_cfg["provider"] == "cursor"
    assert chat_cfg["api_key"] == "chat-key-live"
    assert factory_cfg["api_key"] == "chat-key-live"
    assert chat_cfg["base_url"] == "https://chat.example.test/v1"
    assert chat_cfg["model"] == "chat-model-live"
    assert "api.cursor.com" not in chat_cfg["base_url"]
    assert "api.cursor.com" not in factory_cfg["base_url"]
    assert "error" not in factory_cfg


def test_cursor_key_envs_match_retired_ba_tuple():
    """The tuple survives as the fail-closed cursor-key predicate even
    though the Background-Agent module itself is retired."""
    from app.core.llm_config import CURSOR_KEY_ENVS

    assert set(CURSOR_KEY_ENVS) == {
        "CURSOR_API_KEY",
        "CURSOR_AGENT_API_KEY",
        "FACTORY_CURSOR_API_KEY",
    }
def test_cursor_agent_api_key_is_not_a_chat_credential():
    os.environ["LLM_PROVIDER"] = "cursor"
    os.environ["CURSOR_AGENT_API_KEY"] = "crsr-agent-alias"
    os.environ["CEREBRUM_CHAT_LLM_API_KEY"] = "chat-key-live"
    os.environ["CEREBRUM_CHAT_LLM_BASE_URL"] = "https://chat.example.test/v1"

    cfg = get_llm_config()
    assert cfg["provider"] == "cursor"
    assert cfg["api_key"] == "chat-key-live"
    assert "api.cursor.com" not in cfg["base_url"]


def test_llm_provider_cursor_prefers_cerebrum_chat_over_leftover_kimi():
    """Live Render shape: cursor BA + leftover Kimi + CEREBRUM_CHAT_*."""
    os.environ["LLM_PROVIDER"] = "cursor"
    os.environ["CURSOR_API_KEY"] = "crsr-cursor-http"
    os.environ["KIMI_API_KEY"] = "sk-moonshot-leftover"
    os.environ["CEREBRUM_LLM_API_KEY"] = "sk-moonshot-shared"
    os.environ["CEREBRUM_CHAT_LLM_API_KEY"] = "chat-key-live"
    os.environ["CEREBRUM_CHAT_LLM_BASE_URL"] = "https://chat.example.test/v1"
    os.environ["CEREBRUM_CHAT_LLM_MODEL"] = "chat-model-live"

    chat_cfg = get_llm_config()
    factory_cfg = get_factory_llm_config()

    assert chat_cfg["provider"] == "cursor"
    assert factory_cfg["provider"] == "cursor"
    assert chat_cfg["api_key"] == "chat-key-live"
    assert factory_cfg["api_key"] == "chat-key-live"
    assert chat_cfg["base_url"] == "https://chat.example.test/v1"
    assert chat_cfg["model"] == "chat-model-live"
    assert "api.cursor.com" not in chat_cfg["base_url"]
    assert "api.cursor.com" not in factory_cfg["base_url"]
    assert "error" not in chat_cfg
    assert "error" not in factory_cfg


def test_llm_provider_cursor_without_chat_key_falls_back_to_leftover_moonshot():
    """No CEREBRUM_CHAT_* — leftover Moonshot, never invent Cursor completions."""
    os.environ["LLM_PROVIDER"] = "cursor"
    os.environ["CURSOR_API_KEY"] = "crsr-ba-only"
    os.environ["KIMI_API_KEY"] = "sk-moonshot-leftover"

    chat_cfg = get_llm_config()
    assert chat_cfg["provider"] == "cursor"
    assert chat_cfg["api_key"] == "sk-moonshot-leftover"
    assert "api.cursor.com" not in chat_cfg["base_url"]
    assert "moonshot" in chat_cfg["base_url"]


def test_llm_provider_cursor_without_http_key_errors_and_names_chat_keys():
    os.environ["LLM_PROVIDER"] = "cursor"
    cfg = get_factory_llm_config()
    assert cfg["provider"] == "cursor"
    assert cfg["api_key"] == ""
    error = cfg.get("error", "")
    assert "CEREBRUM_CHAT_LLM_API_KEY" in error
    assert "CURSOR_API_KEY" in error
    assert "api.cursor.com" not in (cfg.get("base_url") or "")


def test_llm_provider_cursor_cerebrum_chat_moonshot_key():
    """Live Render shape after #438: cursor pin + CEREBRUM_CHAT on Moonshot.

    get_llm_config must return provider=cursor with the chat key — never
    empty provider (that logs "No LLM provider configured").
    """
    os.environ["LLM_PROVIDER"] = "cursor"
    os.environ["CEREBRUM_CHAT_LLM_API_KEY"] = "sk-test"
    os.environ["CEREBRUM_CHAT_LLM_BASE_URL"] = "https://api.moonshot.ai/v1"
    os.environ.pop("CEREBRUM_LLM_MOCK", None)
    os.environ.pop("CURSOR_MOCK", None)
    os.environ.pop("KIMI_MOCK", None)
    os.environ.pop("CURSOR_API_KEY", None)

    chat_cfg = get_llm_config()
    factory_cfg = get_factory_llm_config()

    assert chat_cfg["provider"] == "cursor"
    assert chat_cfg["api_key"] == "sk-test"
    assert chat_cfg["base_url"] == "https://api.moonshot.ai/v1"
    assert chat_cfg.get("mock") is False
    assert "api.cursor.com" not in chat_cfg["base_url"]
    assert factory_cfg["provider"] == "cursor"
    assert factory_cfg["api_key"] == "sk-test"
    assert "api.cursor.com" not in factory_cfg["base_url"]
    assert "error" not in chat_cfg


def test_explicit_cursor_not_wiped_when_mock_flag_and_chat_key():
    """CEREBRUM_LLM_MOCK must not drop a live CEREBRUM_CHAT key."""
    os.environ["LLM_PROVIDER"] = "cursor"
    os.environ["CEREBRUM_CHAT_LLM_API_KEY"] = "sk-test"
    os.environ["CEREBRUM_CHAT_LLM_BASE_URL"] = "https://api.moonshot.ai/v1"
    os.environ["CEREBRUM_LLM_MOCK"] = "1"

    cfg = get_llm_config()
    assert cfg["provider"] == "cursor"
    assert cfg["api_key"] == "sk-test"
    assert cfg["base_url"] == "https://api.moonshot.ai/v1"
    assert "api.cursor.com" not in cfg["base_url"]


def test_explicit_cursor_keeps_provider_when_mock_and_no_key():
    """Wipe used to hide LLM_PROVIDER=cursor behind provider=''."""
    os.environ["LLM_PROVIDER"] = "cursor"
    os.environ["CURSOR_MOCK"] = "1"

    cfg = get_llm_config()
    assert cfg["provider"] == "cursor"
    assert cfg["mock"] is True


def test_unknown_provider_still_uses_cerebrum_chat_key():
    """A weird LLM_PROVIDER alias must not ignore a live chat key."""
    os.environ["LLM_PROVIDER"] = "cursor-ba"
    os.environ["CEREBRUM_CHAT_LLM_API_KEY"] = "sk-test"
    os.environ["CEREBRUM_CHAT_LLM_BASE_URL"] = "https://api.moonshot.ai/v1"

    cfg = get_llm_config()
    assert cfg["api_key"] == "sk-test"
    assert cfg["base_url"] == "https://api.moonshot.ai/v1"
    assert cfg["provider"] != ""
    assert "api.cursor.com" not in cfg["base_url"]


def test_deepseek_key_does_not_arm_chat_or_factory_llm():
    """DEEPSEEK_API_KEY is FACTORY_CODE_CLI only — Floor chat stays off it."""
    os.environ["DEEPSEEK_API_KEY"] = _GENERATED_KEY_1
    os.environ.pop("ANTHROPIC_API_KEY", None)
    os.environ.pop("LLM_PROVIDER", None)
    chat_cfg = get_llm_config()
    factory_cfg = get_factory_llm_config()
    assert chat_cfg.get("api_key") != _GENERATED_KEY_1
    assert factory_cfg.get("api_key") != _GENERATED_KEY_1
    # Neither path is ARMED, which is the claim. This used to be asserted as
    # "deepseek.com not in base_url" -- a proxy that only worked because the
    # default host was the retired provider, and which now forbids the legitimate
    # default while proving nothing: a base_url with no key cannot call anything.
    # The absence of a key, and the factory's named fail-closed error, are the
    # direct evidence and a stronger assertion than the proxy was.
    assert not (chat_cfg.get("api_key") or "").strip()
    assert not (factory_cfg.get("api_key") or "").strip()
    assert factory_cfg.get("error"), "the factory path must fail closed and say so"
    assert "DEEPSEEK_API_KEY" in factory_cfg["error"], (
        "the error should tell the operator that this key is the CLI's, not this path's"
    )


def test_chat_http_host_is_hostname_only():
    assert _chat_http_host("https://openrouter.ai/api/v1") == "openrouter.ai"
    assert _chat_http_host("https://user:pw@api.moonshot.ai/v1") == "api.moonshot.ai"
    assert _chat_http_host("api.example.test/v1") == "api.example.test"
    assert _chat_http_host("") == ""
    assert _chat_http_host("   ") == ""


def test_redact_ready_secrets_replaces_known_key_values():
    os.environ["CEREBRUM_CHAT_LLM_API_KEY"] = "sk-or-secret-value"
    text = "missing key sk-or-secret-value after CEREBRUM_CHAT_LLM_API_KEY"
    out = _redact_ready_secrets(text)
    assert "sk-or-secret-value" not in out
    assert "CEREBRUM_CHAT_LLM_API_KEY" in out
    assert "[redacted]" in out


def test_llm_ready_details_redacts_error_that_embeds_the_key():
    os.environ["LLM_PROVIDER"] = "cursor"
    os.environ["CEREBRUM_CHAT_LLM_API_KEY"] = "sk-or-embedded-secret"

    class _Boom(Exception):
        pass

    def _explode():
        raise _Boom("CEREBRUM_CHAT_LLM_API_KEY=sk-or-embedded-secret is unusable")

    import app.core.llm_config as llm_config

    original = llm_config.get_llm_config
    llm_config.get_llm_config = _explode  # type: ignore[method-assign]
    try:
        details = llm_ready_details()
    finally:
        llm_config.get_llm_config = original  # type: ignore[method-assign]
    assert details["cerebrum_chat_llm_api_key_present"] is True
    assert "sk-or-embedded-secret" not in details["chat_http_error"]
    assert "CEREBRUM_CHAT_LLM_API_KEY" in details["chat_http_error"]



def test_deepseek_provider_resolves_scoped_triple_without_moonshot_names(monkeypatch):
    """LLM_PROVIDER=deepseek must resolve the scoped triple and scrub the
    kimi-era fallback-model default so no moonshot/kimi names leak into the
    live config."""
    from app.core import llm_config

    monkeypatch.setenv('LLM_PROVIDER', 'deepseek')
    monkeypatch.setenv('CEREBRUM_FACTORY_LLM_API_KEY', 'sk-ds-test')
    monkeypatch.setenv('CEREBRUM_FACTORY_LLM_BASE_URL', 'https://api.deepseek.com/v1')
    monkeypatch.setenv('CEREBRUM_FACTORY_LLM_MODEL', 'deepseek-chat')
    cfg = llm_config.get_factory_llm_config()
    assert cfg['provider'] == 'deepseek'
    assert cfg['api_key'] == 'sk-ds-test'
    assert 'deepseek.com' in cfg['base_url']
    assert cfg['fallback_model'] == 'deepseek-chat'
    assert not cfg.get('error')


def test_deepseek_provider_uses_deepseek_api_key_without_scoped_triple(monkeypatch):
    """Without a scoped triple the provider falls back to DEEPSEEK_API_KEY."""
    from app.core import llm_config

    monkeypatch.setenv('LLM_PROVIDER', 'deepseek')
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'sk-ds-global')
    cfg = llm_config.get_factory_llm_config()
    assert cfg['provider'] == 'deepseek'
    assert cfg['api_key'] == 'sk-ds-global'
    assert 'deepseek.com' in cfg['base_url']
