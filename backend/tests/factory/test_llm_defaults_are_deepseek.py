"""No configuration may route this Factory to a retired provider.

The defaults were `https://api.moonshot.ai/v1` and `kimi-k2.7-code`. That is not a
cosmetic default: `_scoped_path_endpoint` returns on the FIRST prefix that has an
API key and takes host and model from that prefix alone, so a prefix with a key but
no BASE_URL fell through to the Moonshot constant and the loop never reached the
prefix where the real DeepSeek endpoint was configured. The DeepSeek key was POSTed
to Moonshot, 401'd, and the Floor reported "LLM drafting failed, falling back" --
shipping deterministic-template blueprints while every key in the environment was
valid.

It was worked around live with two env vars. These tests are the fix: DeepSeek is
the default, OpenRouter is the fallback host, and no env shape reaches the retired
provider unless somebody names it explicitly.
"""
from __future__ import annotations

import pytest

from app.core import llm_config

RETIRED = ("moonshot", "kimi-k2", "moonshot-v1")

#: Every env var the resolver reads, cleared before each test so a developer's own
#: shell cannot make these pass or fail.
_ENV = (
    "LLM_PROVIDER",
    "CEREBRUM_LLM_API_KEY", "CEREBRUM_LLM_BASE_URL", "CEREBRUM_LLM_MODEL",
    "CEREBRUM_LLM_FALLBACK_MODEL",
    "CEREBRUM_CHAT_LLM_API_KEY", "CEREBRUM_CHAT_LLM_BASE_URL",
    "CEREBRUM_CHAT_LLM_MODEL", "CEREBRUM_CHAT_LLM_FALLBACK_MODEL",
    "CEREBRUM_FACTORY_LLM_API_KEY", "CEREBRUM_FACTORY_LLM_BASE_URL",
    "CEREBRUM_FACTORY_LLM_MODEL",
    "KIMI_API_KEY", "KIMI_BASE_URL", "KIMI_MODEL", "KIMI_FALLBACK_MODEL",
    "OPENROUTER_API_KEY", "DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY",
    "CURSOR_API_KEY", "FACTORY_LLM_FALLBACK_API_KEY",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)


def _retired_in(value) -> list:
    text = str(value).lower()
    return [marker for marker in RETIRED if marker in text]


def test_the_named_defaults_are_deepseek_and_the_fallback_host_is_openrouter():
    assert llm_config.DEEPSEEK_BASE_URL == "https://api.deepseek.com/v1"
    assert llm_config.DEFAULT_PRIMARY_MODEL == "deepseek-chat"
    assert llm_config.DEFAULT_FALLBACK_MODEL == "deepseek-chat"
    assert "openrouter.ai" in llm_config.OPENROUTER_BASE_URL
    for name in ("DEEPSEEK_BASE_URL", "DEFAULT_PRIMARY_MODEL", "DEFAULT_FALLBACK_MODEL"):
        assert not _retired_in(getattr(llm_config, name)), f"{name} names a retired provider"


def test_a_key_with_no_base_url_resolves_to_deepseek_not_the_retired_host():
    """The exact live shape: a scoped key set, its BASE_URL not set."""
    assert llm_config._kimi_base_url("CEREBRUM_CHAT") == llm_config.DEEPSEEK_BASE_URL
    assert llm_config._kimi_model("CEREBRUM_CHAT") == llm_config.DEFAULT_PRIMARY_MODEL


def test_the_prefix_walk_that_caused_the_live_failure_no_longer_reaches_moonshot(
        monkeypatch):
    """CHAT has a key and no base; FACTORY has the real DeepSeek endpoint. The walk
    still returns on CHAT -- that is its contract -- but what it returns can no
    longer be a retired host."""
    monkeypatch.setenv("CEREBRUM_CHAT_LLM_API_KEY", "sk-a-real-deepseek-key")
    monkeypatch.setenv("CEREBRUM_FACTORY_LLM_API_KEY", "sk-a-real-deepseek-key")
    monkeypatch.setenv("CEREBRUM_FACTORY_LLM_BASE_URL", "https://api.deepseek.com/v1")

    resolved = llm_config._scoped_path_endpoint("CEREBRUM_CHAT", "CEREBRUM_FACTORY")
    assert resolved is not None
    assert not _retired_in(resolved["base_url"]), resolved
    assert not _retired_in(resolved["model"]), resolved
    assert not _retired_in(resolved["fallback_model"]), resolved
    assert resolved["base_url"] == "https://api.deepseek.com/v1"


def test_non_cursor_base_falls_back_to_deepseek_not_the_retired_host():
    assert llm_config._non_cursor_base("") == llm_config.DEEPSEEK_BASE_URL
    assert llm_config._non_cursor_base("https://api.cursor.com") == llm_config.DEEPSEEK_BASE_URL
    # An explicitly configured host is still honoured.
    assert llm_config._non_cursor_base("https://example.test/v1") == "https://example.test/v1"


def test_an_empty_environment_never_produces_a_retired_endpoint():
    """Whatever the resolver returns with nothing configured, it is not Moonshot."""
    config = llm_config.get_llm_config()
    for field in ("base_url", "model", "fallback_model"):
        value = config.get(field) if isinstance(config, dict) else getattr(config, field, "")
        assert not _retired_in(value), f"{field}={value!r} names a retired provider"


def test_the_default_follows_an_explicit_naming_of_the_provider(monkeypatch):
    """The first cut of this fix defaulted everything to DeepSeek unconditionally,
    which mirrored the very bug it fixed: an operator with only KIMI_API_KEY set
    would have that key POSTed to DeepSeek and 401'd. The default follows the
    credential that is actually present."""
    assert llm_config._default_base_url() == llm_config.DEEPSEEK_BASE_URL
    assert llm_config._default_model() == "deepseek-chat"

    monkeypatch.setenv("KIMI_API_KEY", "sk-a-moonshot-key")
    assert llm_config._default_base_url() == llm_config.MOONSHOT_BASE_URL
    assert llm_config._default_model() == "kimi-k2.7-code", (
        "the model must belong to the host the key is sent to"
    )


def test_llm_provider_kimi_also_counts_as_asking_for_it(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "kimi")
    assert llm_config._default_base_url() == llm_config.MOONSHOT_BASE_URL
    monkeypatch.setenv("LLM_PROVIDER", "moonshot")
    assert llm_config._default_base_url() == llm_config.MOONSHOT_BASE_URL
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    assert llm_config._default_base_url() == llm_config.DEEPSEEK_BASE_URL


def test_the_fail_closed_message_names_the_variable_for_the_chosen_provider(monkeypatch):
    """A fail-closed error that points the reader at a retired provider is the one
    thing it must not do -- and it said "requires KIMI_API_KEY" to everyone."""
    from app.core.llm_config import get_factory_llm_config

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-cli-only")
    error = get_factory_llm_config().get("error") or ""
    assert "CEREBRUM_FACTORY_LLM_API_KEY" in error
    assert "DEEPSEEK_API_KEY is the coding CLI's credential" in error
    assert "requires KIMI_API_KEY" not in error

    monkeypatch.setenv("LLM_PROVIDER", "kimi")
    error = get_factory_llm_config().get("error") or ""
    assert "KIMI_API_KEY" in error, "asked for it by name, so name their variable"


def test_a_legacy_kimi_env_var_is_still_honoured_when_somebody_sets_it(monkeypatch):
    """Reading it is not the defect -- silently DEFAULTING to it was. Ignoring a
    value an operator configured would be a defect of its own."""
    monkeypatch.setenv("KIMI_BASE_URL", "https://someone-set-this.test/v1")
    assert llm_config._kimi_base_url("CEREBRUM_CHAT") == "https://someone-set-this.test/v1"


def test_no_retired_default_survives_anywhere_in_the_module():
    """A literal guard. A future edit that reintroduces one fails here rather than
    in production, where it presents as "LLM drafting failed, falling back"."""
    import inspect
    import re

    source = inspect.getsource(llm_config)
    offenders = []
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or ":#" in stripped:
            continue  # the comments explaining this history are allowed to say it
        if re.search(r'default\s*=\s*["\'][^"\']*(moonshot|kimi-k2)', stripped):
            offenders.append(stripped)
        if re.search(r'or\s+["\'][^"\']*(moonshot-v1|kimi-k2)', stripped):
            offenders.append(stripped)
    assert not offenders, "retired-provider default(s) reintroduced: " + "; ".join(offenders)
