"""WK-2: one provider table shared by the config and the coder.

The fault: ``core/llm_config`` resolved a provider name that
``factory/coder`` then refused with "unsupported coder provider", so every
HTTP-coder call raised and the tester's cases and the collector review were
skipped as notes. The fix is one table both sides read. These tests run
config -> coder call for EVERY provider in that table, so a provider added
to the config without the coder being able to call it fails here.

No network: the coder's POST seam is replaced by a recorder. Keys are
generated per run; no value is stored in the repository.
"""

from __future__ import annotations

import os
import re
import secrets

import pytest

from app.core.llm_config import (
    HTTP_CALL_PROVIDERS,
    SUPPORTED_PROVIDERS,
    get_factory_llm_config,
)
from app.factory import coder

#: Every setting that can steer provider selection is cleared first, so the
#: test sees only what it sets (CI and a developer shell differ otherwise).
_STEERING = re.compile(
    r"^(LLM_|KIMI_|MOONSHOT_|ANTHROPIC_|CLAUDE_|CURSOR_|DEEPSEEK_|OPENROUTER_|"
    r"FACTORY_LLM_|FACTORY_CURSOR_|FACTORY_CODE_|CEREBRUM_)"
)

_ANSWER = "print('written by the provider under test')"


def _clean_env(monkeypatch) -> None:
    for name in list(os.environ):
        if _STEERING.match(name):
            monkeypatch.delenv(name, raising=False)
    # No cross-provider leg: the primary alone must be callable.
    monkeypatch.setenv("FACTORY_LLM_FALLBACK_PROVIDER", "none")


def _arm_every_key(monkeypatch) -> None:
    """A generated key in every slot a provider reads, so the provider named
    by LLM_PROVIDER is the only thing that differs between cases."""
    for slot in (
        "DEEPSEEK_API_KEY",
        "CEREBRUM_LLM_API_KEY",
        "CEREBRUM_CHAT_LLM_API_KEY",
        "ANTHROPIC_API_KEY",
    ):
        monkeypatch.setenv(slot, "sk-" + secrets.token_hex(12))


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return _Resp()


class _Resp:
    status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        # Both wire shapes: OpenAI-compatible and Anthropic Messages.
        return {
            "choices": [{"message": {"content": _ANSWER}, "finish_reason": "stop"}],
            "content": [{"type": "text", "text": _ANSWER}],
        }


def test_config_and_coder_read_one_table():
    assert HTTP_CALL_PROVIDERS is SUPPORTED_PROVIDERS


@pytest.mark.parametrize("provider", list(HTTP_CALL_PROVIDERS))
def test_every_config_provider_reaches_the_coder(monkeypatch, provider):
    _clean_env(monkeypatch)
    _arm_every_key(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", provider)

    cfg = get_factory_llm_config()
    assert cfg.get("error") is None, cfg.get("error")
    assert cfg["provider"] == provider

    rec = _Recorder()
    monkeypatch.setattr(coder, "post_with_deadline", rec)
    # The impl directly, with no attempt deadline set by the wrapper: the
    # impl must set its own deadline rather than run unbounded.
    text, model = coder._llm_code_call_impl([{"role": "user", "content": "u"}])

    assert text == _ANSWER
    assert model == cfg["model"]
    assert len(rec.calls) == 1
    assert rec.calls[0]["url"].startswith(cfg["base_url"].rstrip("/"))
    assert isinstance(rec.calls[0]["deadline_mono"], float)


@pytest.mark.parametrize(
    ("provider", "mock_flag"),
    [("claude", "CLAUDE_MOCK"), ("cursor", "CEREBRUM_LLM_MOCK")],
)
def test_mock_config_is_refused_before_any_call(monkeypatch, provider, mock_flag):
    _clean_env(monkeypatch)
    _arm_every_key(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", provider)
    monkeypatch.setenv(mock_flag, "1")
    rec = _Recorder()
    monkeypatch.setattr(coder, "post_with_deadline", rec)
    assert get_factory_llm_config()["mock"] is True

    with pytest.raises(coder.CoderError, match="mock"):
        coder._llm_code_call_impl([{"role": "user", "content": "u"}])
    assert rec.calls == []


@pytest.mark.parametrize("key_slot", ["CEREBRUM_LLM_API_KEY", "CEREBRUM_CHAT_LLM_API_KEY"])
def test_unnamed_provider_does_not_default_to_the_retired_host(monkeypatch, key_slot):
    _clean_env(monkeypatch)
    monkeypatch.setenv(key_slot, "sk-" + secrets.token_hex(12))

    cfg = get_factory_llm_config()

    assert cfg["provider"] in HTTP_CALL_PROVIDERS
    assert "moonshot" not in cfg["base_url"].lower()
    assert "kimi" not in cfg["model"].lower()


# -- Grader #252 bar 4: the cases that were missing --------------------------

import ast  # noqa: E402
import asyncio  # noqa: E402
from pathlib import Path  # noqa: E402

from app.core import llm_config  # noqa: E402
from app.core.llm_config import (  # noqa: E402
    OPENAI_WIRE_PROVIDERS,
    PROVIDER_TABLE,
    ProviderConfigError,
    get_factory_fallback_leg,
    get_llm_config,
    provider_setting_errors,
    validate_provider_at_boot,
)

#: Names the table refuses: an unknown one and the retired vendor's names.
_REFUSED_NAMES = ("qwen", "kimi", "moonshot")


def _no_call_recorder(monkeypatch) -> _Recorder:
    rec = _Recorder()
    monkeypatch.setattr(coder, "post_with_deadline", rec)
    return rec


@pytest.mark.parametrize("name", _REFUSED_NAMES)
def test_a_name_outside_the_table_is_refused_with_no_call(monkeypatch, name):
    """Unknown and retired names alike: an error naming the setting, a config
    error (not an absence), no endpoint, and the coder makes no call."""
    _clean_env(monkeypatch)
    _arm_every_key(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", name)
    for cfg in (get_llm_config(), get_factory_llm_config()):
        assert cfg["provider"] == ""
        assert cfg["api_key"] == "" and cfg["base_url"] == ""
        assert f"LLM_PROVIDER={name} is not in the provider table" in cfg["error"]
        assert cfg["config_error"] is True
    rec = _no_call_recorder(monkeypatch)
    with pytest.raises(coder.CoderError, match="not in the provider table"):
        coder._llm_code_call_impl([{"role": "user", "content": "u"}])
    assert rec.calls == []


@pytest.mark.parametrize(
    ("key_slot", "expected"),
    [
        ("CEREBRUM_LLM_API_KEY", "deepseek"),
        ("CEREBRUM_CHAT_LLM_API_KEY", "deepseek"),
        ("CEREBRUM_FACTORY_LLM_API_KEY", "deepseek"),
        ("ANTHROPIC_API_KEY", "claude"),
    ],
)
def test_unset_provider_with_one_vendors_key_resolves_to_that_vendor(
    monkeypatch, key_slot, expected
):
    _clean_env(monkeypatch)
    monkeypatch.setenv(key_slot, "sk-" + secrets.token_hex(12))
    cfg = get_factory_llm_config()
    assert cfg.get("error") is None, cfg.get("error")
    assert cfg["provider"] == expected
    assert PROVIDER_TABLE[expected]["role"] == "primary"


def _arm_free_leg(monkeypatch) -> None:
    monkeypatch.delenv("FACTORY_LLM_FALLBACK_PROVIDER", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-" + secrets.token_hex(12))
    leg = get_factory_fallback_leg()
    assert leg and leg["is_free"] and "error" not in leg


_SHARED_SLOTS = ("CEREBRUM_LLM_API_KEY", "CEREBRUM_CHAT_LLM_API_KEY")
_CLAUDE_SLOTS = ("ANTHROPIC_API_KEY", "CLAUDE_API_KEY")


@pytest.mark.parametrize(
    ("shared_slot", "claude_slot"),
    [(_SHARED_SLOTS[0], _CLAUDE_SLOTS[0]), (_SHARED_SLOTS[1], _CLAUDE_SLOTS[1])],
)
def test_two_vendors_keys_are_ambiguous_and_the_free_leg_does_not_hide_it(
    monkeypatch, shared_slot, claude_slot
):
    """The ambiguity is a config error. A zero-cost fallback leg is armed
    here on purpose: the coder must still raise, not answer from the leg."""
    _clean_env(monkeypatch)
    monkeypatch.setenv(shared_slot, "sk-" + secrets.token_hex(12))
    monkeypatch.setenv(claude_slot, "sk-" + secrets.token_hex(12))
    _arm_free_leg(monkeypatch)

    cfg = get_factory_llm_config()
    assert cfg["provider"] == ""
    assert "more than one provider" in cfg["error"]
    assert "deepseek and claude" in cfg["error"]
    assert cfg["config_error"] is True

    rec = _no_call_recorder(monkeypatch)
    with pytest.raises(coder.CoderError, match="more than one provider"):
        coder._llm_code_call_impl([{"role": "user", "content": "u"}])
    assert rec.calls == []


@pytest.mark.parametrize("leg_armed", [False, True])
def test_no_key_is_an_absence_not_a_config_error(monkeypatch, leg_armed):
    """No key at all: an error naming the settings, but NOT a config error --
    the zero-cost leg, when armed, may still carry a keyless deployment;
    when not armed, the coder raises that error with no call."""
    _clean_env(monkeypatch)
    cfg = get_factory_llm_config()
    assert cfg["provider"] == ""
    assert "no provider credentials are present" in cfg["error"]
    assert "LLM_PROVIDER" in cfg["error"]
    assert cfg["config_error"] is False

    if leg_armed:
        _arm_free_leg(monkeypatch)
    rec = _no_call_recorder(monkeypatch)
    if not leg_armed:
        with pytest.raises(coder.CoderError, match="no provider credentials"):
            coder._llm_code_call_impl([{"role": "user", "content": "u"}])
        assert rec.calls == []
        return
    text, _model = coder._llm_code_call_impl([{"role": "user", "content": "u"}])
    assert text == _ANSWER
    assert len(rec.calls) == 1


@pytest.mark.parametrize("name", ["deepseek", "claude", "kimi"])
def test_a_fallback_name_outside_the_fallback_role_is_refused(monkeypatch, name):
    _clean_env(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-" + secrets.token_hex(12))
    monkeypatch.setenv("FACTORY_LLM_FALLBACK_PROVIDER", name)
    leg = get_factory_fallback_leg()
    assert leg["base_url"] == "" and leg["api_key"] == ""
    assert f"FACTORY_LLM_FALLBACK_PROVIDER={name}" in leg["error"]
    with pytest.raises(ProviderConfigError, match="FACTORY_LLM_FALLBACK_PROVIDER"):
        validate_provider_at_boot()


# -- the architect's claude branch, and the branch removed on purpose --------


@pytest.mark.parametrize(
    ("name", "key_slot"),
    [("claude", "ANTHROPIC_API_KEY"), ("anthropic", "CLAUDE_API_KEY")],
)
def test_the_architect_posts_the_messages_api_for_claude(monkeypatch, name, key_slot):
    from app.factory import llm_watchdog, product_architect

    _clean_env(monkeypatch)
    key = "sk-" + secrets.token_hex(12)
    monkeypatch.setenv("LLM_PROVIDER", name)
    monkeypatch.setenv(key_slot, key)
    sent: list[dict] = []

    class _Claude:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"content": [{"type": "text", "text": '{"answer": 1}'}]}

    def _post(url, **kwargs):
        sent.append({"url": url, **kwargs})
        return _Claude()

    monkeypatch.setattr(llm_watchdog, "post_with_deadline", _post)
    out = product_architect._llm_json_call(
        [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    )
    assert out == {"answer": 1}
    assert len(sent) == 1
    assert sent[0]["url"].endswith("/messages")
    assert sent[0]["headers"]["x-api-key"] == key
    assert "Authorization" not in sent[0]["headers"]
    assert sent[0]["json"]["system"] == "s"
    assert all(m["role"] != "system" for m in sent[0]["json"]["messages"])


@pytest.mark.parametrize("nameless", ["", None])
def test_the_architect_no_longer_calls_a_nameless_key_and_host(monkeypatch, nameless):
    """Removed ON PURPOSE (WK-2): a config with a key and a host but no
    provider name used to be called. It is not in the table, so it is refused."""
    from app.core.model_ladder import ModelUnavailable
    from app.factory import llm_watchdog, product_architect

    _clean_env(monkeypatch)
    monkeypatch.setattr(
        product_architect,
        "get_factory_llm_config",
        lambda: {
            "provider": nameless,
            "api_key": "sk-" + secrets.token_hex(12),
            "base_url": "https://llm.example.test/v1",
            "model": "m",
        },
    )
    sent: list = []
    monkeypatch.setattr(llm_watchdog, "post_with_deadline", lambda *a, **k: sent.append(a))
    with pytest.raises(ModelUnavailable):
        product_architect._llm_json_call([{"role": "user", "content": "u"}])
    assert sent == []


@pytest.mark.parametrize(
    ("shared_slot", "claude_slot"),
    [(_SHARED_SLOTS[0], _CLAUDE_SLOTS[0]), (_SHARED_SLOTS[1], _CLAUDE_SLOTS[1])],
)
def test_the_architect_raises_an_ambiguous_config_even_with_a_free_leg(
    monkeypatch, shared_slot, claude_slot
):
    from app.core.model_ladder import ModelUnavailable
    from app.factory import llm_watchdog, product_architect

    _clean_env(monkeypatch)
    monkeypatch.setenv(shared_slot, "sk-" + secrets.token_hex(12))
    monkeypatch.setenv(claude_slot, "sk-" + secrets.token_hex(12))
    _arm_free_leg(monkeypatch)
    sent: list = []
    monkeypatch.setattr(llm_watchdog, "post_with_deadline", lambda *a, **k: sent.append(a))
    with pytest.raises(ModelUnavailable, match="more than one provider"):
        product_architect._llm_json_call([{"role": "user", "content": "u"}])
    assert sent == []


# -- the chain generator and the rule parser read the table ------------------


@pytest.mark.parametrize("provider", [*PROVIDER_TABLE, *_REFUSED_NAMES])
def test_the_chain_generator_takes_exactly_the_tables_openai_wire(provider):
    from app.core import chain_generator

    cfg = {"provider": provider, "api_key": "k", "base_url": "https://llm.example.test/v1"}
    expected = provider in PROVIDER_TABLE and PROVIDER_TABLE[provider]["wire"] == "openai"
    assert chain_generator._is_usable_openai_chat_cfg(cfg) is expected


@pytest.mark.parametrize("provider", [*PROVIDER_TABLE, *_REFUSED_NAMES])
def test_the_rule_parser_takes_exactly_the_tables_openai_wire(monkeypatch, provider):
    from app.core import rule_parser

    calls: list = []

    def _sync(base_url, api_key, model, messages):
        calls.append(base_url)
        return {"rules": [{"raw": "r", "trigger": "t", "action": "a"}]}

    monkeypatch.setattr(rule_parser, "_call_openai_sync", _sync)
    monkeypatch.setattr(
        rule_parser,
        "get_llm_config",
        lambda: {"base_url": "https://llm.example.test/v1", "api_key": "k", "model": "m"},
    )
    rule_parser._parse_with_llm(provider, ["r"])
    in_table = provider in PROVIDER_TABLE and PROVIDER_TABLE[provider]["wire"] == "openai"
    assert bool(calls) is in_table
    assert set(OPENAI_WIRE_PROVIDERS) == {
        n for n, row in PROVIDER_TABLE.items() if row["wire"] == "openai"
    }


# -- no second provider list anywhere under backend/app ----------------------

_APP = Path(__file__).resolve().parents[2] / "app"

#: Provider names a hard-coded list would spell: the table's own names, the
#: one alias, and the retired vendor's names.
_VOCAB = frozenset(PROVIDER_TABLE) | {"anthropic", "kimi", "moonshot"}

#: Second lists still pending removal. Item 1c (the coding-CLI vehicle and
#: the admin coder picker) emptied it; each entry must STILL trip the scan,
#: so this set can only shrink and stays empty.
_PENDING_1C: set = set()


def _enclosing(tree: ast.AST) -> dict:
    owner: dict = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for child in ast.walk(node):
                owner.setdefault(id(child), node.name)
    return owner


def _second_lists(root: Path) -> set:
    found = set()
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        owner = _enclosing(tree)
        rel = path.relative_to(root).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Set, ast.List, ast.Tuple)):
                elts = node.elts
            elif isinstance(node, ast.Dict):
                elts = [k for k in node.keys if k is not None]
            else:
                continue
            names = {
                e.value.strip().lower()
                for e in elts
                if isinstance(e, ast.Constant) and isinstance(e.value, str)
            }
            if len(names & _VOCAB) < 2:
                continue
            if (
                rel == "core/llm_config.py"
                and isinstance(node, ast.Dict)
                and names == set(PROVIDER_TABLE)
            ):
                continue  # THE table itself
            found.add((rel, owner.get(id(node), "<module>")))
    return found


@pytest.mark.parametrize("check", ["no_new_list", "pending_still_trips"])
def test_no_second_provider_list_exists_under_backend_app(check):
    """Scans the code, not an identity: any set, list, tuple or dict literal
    spelling two or more provider names, other than PROVIDER_TABLE, fails.
    Each pending entry must still trip the scan, so the set can only shrink."""
    found = _second_lists(_APP)
    if check == "no_new_list":
        assert found - _PENDING_1C == set(), sorted(found - _PENDING_1C)
    else:
        assert _PENDING_1C - found == set(), (
            "a pending entry no longer trips the scan: remove it from _PENDING_1C"
        )


@pytest.mark.parametrize(
    "source",
    [
        'def pick(p):\n    return p in ("deepseek", "claude")\n',
        'def pick(p):\n    return p in {"anthropic", "openrouter"}\n',
        'def pick(p):\n    return {"deepseek": 1, "cursor": 2}.get(p)\n',
    ],
)
def test_the_scan_catches_a_planted_second_list(tmp_path, source):
    """The scan is live: a planted list of any literal shape is found."""
    (tmp_path / "planted.py").write_text(source, encoding="utf-8")
    assert _second_lists(tmp_path) == {("planted.py", "pick")}


# -- boot validation ---------------------------------------------------------


@pytest.mark.parametrize("name", _REFUSED_NAMES)
def test_boot_refuses_a_provider_name_outside_the_table(monkeypatch, name):
    _clean_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", name)
    with pytest.raises(ProviderConfigError, match=f"LLM_PROVIDER={name}"):
        validate_provider_at_boot()


def test_boot_refuses_two_vendors_keys_with_no_name(monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv("CEREBRUM_LLM_API_KEY", "sk-" + secrets.token_hex(12))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-" + secrets.token_hex(12))
    with pytest.raises(ProviderConfigError, match="more than one provider"):
        validate_provider_at_boot()
    monkeypatch.setenv("LLM_PROVIDER", "claude")
    validate_provider_at_boot()  # naming one resolves it


@pytest.mark.parametrize("name", [*HTTP_CALL_PROVIDERS, "Anthropic", ""])
def test_boot_accepts_a_table_name_or_no_name(monkeypatch, name):
    _clean_env(monkeypatch)
    monkeypatch.delenv("FACTORY_LLM_FALLBACK_PROVIDER", raising=False)
    if name:
        monkeypatch.setenv("LLM_PROVIDER", name)
    assert provider_setting_errors() == []
    validate_provider_at_boot()


@pytest.mark.parametrize("value", ["none", "off", "disabled", "openrouter", "OpenRouter"])
def test_boot_accepts_a_disabled_or_table_fallback_leg(monkeypatch, value):
    _clean_env(monkeypatch)
    monkeypatch.setenv("FACTORY_LLM_FALLBACK_PROVIDER", value)
    assert provider_setting_errors() == []


@pytest.mark.parametrize("name", ["kimi", "qwen"])
def test_the_app_start_runs_the_boot_validation_first(monkeypatch, name):
    """Wired into the real lifespan, before anything else starts."""
    from app import main

    _clean_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", name)
    started: list = []
    monkeypatch.setattr(main.backup_scheduler, "start", lambda: started.append(1))

    async def _enter():
        async with main._lifespan(main.app):
            pass

    with pytest.raises(ProviderConfigError, match=f"LLM_PROVIDER={name}"):
        asyncio.run(_enter())
    assert started == []
    assert llm_config.validate_provider_at_boot is main.validate_provider_at_boot


# -- the retired vendor's name is not spelled as a bare string constant -------

#: The retired vendor's names (the same two the table refuses above).
_RETIRED = frozenset({"kimi", "moonshot"})

#: Bare retired-name constants still pending removal. Item 1c (the
#: coding-CLI vehicle, its session wiring and the admin coder picker) emptied
#: it; each entry must STILL trip the scan, so this set can only shrink and
#: stays empty.
_PENDING_1C_RETIRED: set = set()


def _retired_constants(root: Path) -> set:
    found = set()
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        owner = _enclosing(tree)
        rel = path.relative_to(root).as_posix()
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value.strip().lower() in _RETIRED
            ):
                found.add((rel, owner.get(id(node), "<module>")))
    return found


@pytest.mark.parametrize("check", ["no_new_constant", "pending_still_trips"])
def test_no_bare_retired_name_constant_under_backend_app(check):
    """Any string constant under backend/app whose whole value is a retired
    name fails, outside the pending item-1c entries (which can only shrink)."""
    found = _retired_constants(_APP)
    if check == "no_new_constant":
        assert found - _PENDING_1C_RETIRED == set(), sorted(found - _PENDING_1C_RETIRED)
    else:
        assert _PENDING_1C_RETIRED - found == set(), (
            "a pending entry no longer trips the scan: remove it from _PENDING_1C_RETIRED"
        )


@pytest.mark.parametrize(
    "source",
    [
        'def pick(p):\n    return p == "kimi"\n',
        'def pick(p):\n    return p.lower() == " Moonshot "\n',
        'def pick(p):\n    return {"KIMI": 1}.get(p)\n',
    ],
)
def test_the_scan_catches_a_planted_retired_constant(tmp_path, source):
    """The scan is live: a planted bare retired name is found in any case."""
    (tmp_path / "planted.py").write_text(source, encoding="utf-8")
    assert _retired_constants(tmp_path) == {("planted.py", "pick")}


@pytest.mark.parametrize(
    "source",
    [
        'def pick(p):\n    return "kimi was retired" if p else ""\n',
        'def pick(p):\n    return p == "moonshot-v1-8k"\n',
    ],
)
def test_the_scan_ignores_a_retired_name_inside_longer_text(tmp_path, source):
    """Only a bare name trips it; longer text that contains one does not."""
    (tmp_path / "prose.py").write_text(source, encoding="utf-8")
    assert _retired_constants(tmp_path) == set()


# -- /ready's llm_configured counts only keys the table reads -----------------


def test_the_retired_vendors_key_alone_is_not_reported_configured(monkeypatch):
    """The table ignores the retired vendor's key, so /ready must not report
    the model configured on it."""
    from app.main import llm_key_configured

    _clean_env(monkeypatch)
    monkeypatch.setenv("KIMI_API_KEY", "sk-" + secrets.token_hex(12))
    assert llm_key_configured() is False


@pytest.mark.parametrize(
    "slot", ["CEREBRUM_LLM_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY"]
)
def test_a_table_key_alone_is_reported_configured(monkeypatch, slot):
    from app.main import llm_key_configured

    _clean_env(monkeypatch)
    assert llm_key_configured() is False
    monkeypatch.setenv(slot, "sk-" + secrets.token_hex(12))
    assert llm_key_configured() is True


# -- the retired name anywhere it is carried (grader #256 bar 5, item 3) ------
#
# The bare-constant scan above misses identifiers (``kimi_credentials_file``),
# setting names (``KIMI_CODE_CLI``), file names (``setup_kimi_env.sh``) and
# prose. This scan reads every text file in the repository -- product code,
# config, env examples, scripts, generator templates, frontend and docs --
# and counts, per file, the lines that carry a retired name in any case,
# plus one for each retired name in the file's own name. Test code is not
# scanned (it asserts the refusal). Generated and vendored trees are skipped.

_REPO = Path(__file__).resolve().parents[3]

_CARRIES_RETIRED = re.compile("|".join(sorted(_RETIRED)), re.IGNORECASE)

#: Tool caches are skipped by name wherever a tool writes them: no source
#: file lives inside one.
_SKIP_DIRS = frozenset({"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"})
#: Everything else is skipped only at the path where it is generated or
#: vendored (``.gitignore`` and the frontend/backend tool configs), never by
#: a bare directory name: a ``dist``, ``coverage`` or ``venv`` folder anywhere
#: else in the tree is scanned.
_SKIP_PATHS = frozenset(
    {
        "backend/tests", "tests",  # test code asserts the refusal
        ".git", "factory_outputs",
        "venv", ".venv", "backend/venv", "backend/.venv", "backend/.venv-factory",
        "backend/htmlcov", "htmlcov",
        "node_modules", "frontend/node_modules", "frontend/dist",
        "frontend/coverage", "frontend/test-results", "frontend/playwright-report",
    }
)

#: Item 1c-2's work list: every file that still carries a retired name, with
#: its exact count. The scan must find exactly this map: a file not listed,
#: a count above OR below its entry, and an entry whose file no longer
#: carries the name all fail. A dropped count must be lowered here in the
#: same change, so no entry keeps slack a file could regrow into. Item 1c-2
#: empties it.
_PENDING_1C2_CARRIERS: dict = {
    ".github/workflows/ci.yml": 4,
    "AUDIT.md": 1,
    "KNOWN_INCOMPLETE.md": 12,
    "KNOWN_LIMITATIONS.md": 3,
    "README.md": 6,
    "backend/app/core/deployer.py": 1,
    "backend/app/factory/build/agent_confine.py": 9,
    "backend/app/factory/build/authorship.py": 1,
    "backend/app/factory/build/budget_inspect.py": 2,
    "backend/app/factory/build/coder_session.py": 184,
    "backend/app/factory/build/probe_set.json": 1,
    "backend/app/factory/build/roles_handlers.py": 7,
    "backend/app/factory/build/writer_phases.py": 1,
    "backend/app/factory/code_cli.py": 42,
    "backend/app/factory/standards/domain_packs/kernel_wave1_trust_spine.md": 1,
    "backend/app/routers/delivery_standard.py": 1,
    "backend/app/workbench/__init__.py": 2,
    "backend/app/workbench/agent.py": 40,
    "backend/app/workbench/candidate.py": 2,
    "backend/app/workbench/flags.py": 2,
    "backend/app/workbench/router.py": 2,
    "backend/app/workbench/session.py": 4,
    "build/stages/S1_root_cause.json": 1,
    "docs/FREE_TRIAL_READINESS_REPORT.md": 5,
    "docs/GUARDRAILS.md": 3,
    "docs/LIVE_TEST_READY.md": 6,
    "docs/PHASE2_SCOUT_LAW.md": 1,
    "docs/PRODUCT_DELIVERY_STANDARD.md": 2,
    "docs/PUBLIC_LAUNCH_READINESS.md": 2,
    "docs/UNIVERSAL_KERNEL.md": 1,
    "docs/audits/2026-07-24-platform-sweep-report.md": 7,
    "docs/audits/REGISTERED_BUT_DEAD_AUDIT.md": 2,
    "docs/factory/DEEPSEEK_ENV_SETUP.md": 33,
    "docs/factory/HONEST_STUBS.md": 2,
    "docs/factory/KIMI_ENV_SETUP.md": 54,
    "docs/factory/MR_FINANCE_GROUNDING_AND_GATING.md": 3,
    "docs/legal/PRIVACY_POLICY.md": 1,
    "docs/reports/MARKET_READINESS_AUDIT.md": 1,
    "docs/resident-engineer/M4-build-mode-workbench.md": 1,
    "docs/resident-engineer/STANDING_ORDERS.md": 3,
    "docs/runbook.md": 2,
    "docs/superpowers/specs/2026-07-08-domain-v2-chain-quality-check.md": 1,
    "frontend/e2e/factory_surfaces.spec.ts": 4,
    "frontend/e2e/floor_composer_visible.spec.ts": 1,
    "frontend/e2e/floor_mobile_overflow.spec.ts": 1,
    "frontend/e2e/floor_upload.spec.ts": 1,
    "frontend/e2e/production_factory.spec.ts": 1,
    "frontend/src/__tests__/build-progress.test.ts": 4,
    "frontend/src/__tests__/floor.test.tsx": 2,
    "frontend/src/__tests__/platforms.test.tsx": 5,
    "frontend/src/accountViews.tsx": 1,
    "frontend/src/api/factory.ts": 1,
    "frontend/src/buildProgress.ts": 2,
    "render.yaml": 2,
    "runtime/engine.py": 6,
    "scripts/render_create.py": 1,
    "scripts/setup_kimi_code_env.sh": 48,
    "scripts/setup_kimi_env.sh": 3,
}


def _retired_carriers(root: Path) -> dict:
    found: dict = {}

    def walk(folder: Path) -> None:
        for path in sorted(folder.iterdir()):
            rel = path.relative_to(root).as_posix()
            if path.is_dir():
                if path.name not in _SKIP_DIRS and rel not in _SKIP_PATHS:
                    walk(path)
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue  # binary
            count = sum(1 for line in text.splitlines() if _CARRIES_RETIRED.search(line))
            count += len(_CARRIES_RETIRED.findall(path.name))
            if count:
                found[rel] = count

    walk(root)
    return found


def _carrier_drift(found: dict, pending: dict) -> dict:
    """Every file whose found count differs from its pending entry, either way."""
    return {
        rel: {"found": found.get(rel, 0), "pending": pending.get(rel, 0)}
        for rel in sorted(set(found) | set(pending))
        if found.get(rel, 0) != pending.get(rel, 0)
    }


@pytest.mark.parametrize("tree", ["this_repository", "a_clean_tree"])
def test_the_retired_name_carriers_equal_the_pending_list(tmp_path, tree):
    """This repository must carry exactly the pending map; a tree with no
    carrier must carry exactly the empty map (where item 1c-2 ends)."""
    if tree == "this_repository":
        root, pending = _REPO, _PENDING_1C2_CARRIERS
    else:
        (tmp_path / "a.py").write_text("provider = 'deepseek'\n", encoding="utf-8")
        root, pending = tmp_path, {}
    drift = _carrier_drift(_retired_carriers(root), pending)
    assert drift == {}, f"set each entry to the found count (0 = remove it): {drift}"


@pytest.mark.parametrize(
    ("pending", "expected"),
    [
        ({"a.py": 2}, {}),
        ({"a.py": 3}, {"a.py": {"found": 2, "pending": 3}}),  # a count that dropped
        ({"a.py": 1}, {"a.py": {"found": 2, "pending": 1}}),  # a count that grew
        ({}, {"a.py": {"found": 2, "pending": 0}}),  # a new carrier
        ({"a.py": 2, "b.md": 1}, {"b.md": {"found": 0, "pending": 1}}),  # stale entry
    ],
)
def test_a_count_that_moves_either_way_is_drift(tmp_path, pending, expected):
    """A dropped count fails like a grown one: no slack is left to regrow into."""
    (tmp_path / "a.py").write_text("KIMI_CODE_CLI\nMOONSHOT\n", encoding="utf-8")
    assert _carrier_drift(_retired_carriers(tmp_path), pending) == expected


@pytest.mark.parametrize(
    ("rel", "content", "expected"),
    [
        ("app/x.py", "path = kimi_credentials_file()\n", 1),
        ("frontend/src/a.ts", "const v = env.KIMI_CODE_CLI;\nok\nMoonshot\n", 2),
        ("scripts/setup_kimi_env.sh", "echo ok\n", 1),
        ("docs/notes.md", "nothing retired here\n", 0),
    ],
)
def test_the_carrier_scan_catches_identifiers_settings_and_file_names(
    tmp_path, rel, content, expected
):
    """Live: an identifier, a setting name, a file name, any case."""
    target = tmp_path / rel
    target.parent.mkdir(parents=True)
    target.write_text(content, encoding="utf-8")
    assert _retired_carriers(tmp_path) == ({rel: expected} if expected else {})


@pytest.mark.parametrize(
    "skipped",
    [
        "tests/t.py",
        "backend/tests/t.py",
        "frontend/node_modules/m.js",
        "frontend/dist/a.js",
        "frontend/coverage/c.json",
        "backend/venv/lib/v.py",
        "backend/app/__pycache__/x.py",
    ],
)
def test_the_carrier_scan_skips_test_and_vendored_trees(tmp_path, skipped):
    target = tmp_path / skipped
    target.parent.mkdir(parents=True)
    target.write_text("KIMI_CODE_CLI\n", encoding="utf-8")
    (tmp_path / "kept.py").write_text("KIMI_CODE_CLI\n", encoding="utf-8")
    assert _retired_carriers(tmp_path) == {"kept.py": 1}


@pytest.mark.parametrize(
    "scanned",
    ["backend/app/dist/d.py", "docs/coverage/c.md", "scripts/venv/v.sh", "frontend/src/dist/a.ts"],
)
def test_a_generated_dir_name_elsewhere_in_the_tree_is_scanned(tmp_path, scanned):
    """The skip is anchored to the generated path, not to the directory name."""
    target = tmp_path / scanned
    target.parent.mkdir(parents=True)
    target.write_text("KIMI_CODE_CLI\n", encoding="utf-8")
    assert _retired_carriers(tmp_path) == {scanned: 1}
