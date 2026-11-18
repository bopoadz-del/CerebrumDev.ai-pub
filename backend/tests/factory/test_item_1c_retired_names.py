"""Item 1c: the retired vendor is never a coder provider, default or option.

Direct, fast tests for each function item 1c touched in the coding-CLI seam
(``app.factory.code_cli``), its session wiring
(``app.factory.build.coder_session``) and the admin coder picker
(``app.routers.admin_ops``). No subprocess runs here: each test names the
function it pins and asserts its exact result for two inputs.
"""

from __future__ import annotations

import importlib
import secrets

import pytest

from app.core.llm_config import PROVIDER_TABLE
from app.factory import code_cli
from app.factory.build import coder_session

_RETIRED = ("kimi", "moonshot")


def _clean(monkeypatch) -> None:
    for name in (
        "FACTORY_CODE_CLI",
        "KIMI_CODE_CLI",
        "FACTORY_CODE_PROVIDER",
        "DEEPSEEK_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)


def _key() -> str:
    return "sk-" + secrets.token_hex(12)


# -- code_cli ----------------------------------------------------------------


@pytest.mark.parametrize(
    "setting, expected",
    [("anthropic", "claude"), ("OpenRouter", "openrouter"), ("deepseek", "deepseek")],
)
def test_factory_code_provider_takes_a_table_name(monkeypatch, setting, expected):
    _clean(monkeypatch)
    monkeypatch.setenv("FACTORY_CODE_PROVIDER", setting)
    assert code_cli.factory_code_provider() == expected


@pytest.mark.parametrize("setting", [*_RETIRED, "qwen"])
def test_a_setting_outside_the_table_fails_closed_with_another_key_present(
    monkeypatch, setting
):
    """Grader #256 bar 5 (inverted on purpose): a non-empty setting outside
    the table selects NO provider, even with the DeepSeek key and a claude
    binary present; it never falls through to another provider."""
    _clean(monkeypatch)
    monkeypatch.setenv("FACTORY_CODE_PROVIDER", setting)
    assert code_cli.factory_code_provider() == ""
    monkeypatch.setenv("DEEPSEEK_API_KEY", _key())
    monkeypatch.setenv("FACTORY_CODE_CLI", "/usr/bin/claude")
    assert code_cli.factory_code_provider() == ""
    assert code_cli.deepseek_coder_selected("/usr/bin/claude") is False
    assert code_cli.deepseek_coder_selected("/opt/coder") is False


@pytest.mark.parametrize("setting, expected", [("qwen", False), ("deepseek", True)])
def test_the_credential_path_refuses_a_setting_outside_the_table(
    monkeypatch, setting, expected
):
    """The same DeepSeek key: a table name passes the credential check, a
    name outside the table is refused and the operator text names it."""
    _clean(monkeypatch)
    monkeypatch.setenv("DEEPSEEK_API_KEY", _key())
    monkeypatch.setenv("FACTORY_CODE_PROVIDER", setting)
    assert coder_session.cli_credentials_ok("/opt/coder") is expected
    named = f"FACTORY_CODE_PROVIDER={setting} is not a provider" in (
        coder_session._table_provider_credentials()
    )
    assert named is not expected


@pytest.mark.parametrize("cli, expected", [("/usr/bin/claude", "claude"), ("/opt/coder", "")])
def test_factory_code_provider_with_no_setting_has_no_default(monkeypatch, cli, expected):
    _clean(monkeypatch)
    monkeypatch.setenv("FACTORY_CODE_CLI", cli)
    assert code_cli.factory_code_provider() == expected


@pytest.mark.parametrize("setting", ["kimi", "qwen"])
def test_code_provider_setting_error_names_the_setting_and_the_table(monkeypatch, setting):
    _clean(monkeypatch)
    monkeypatch.setenv("FACTORY_CODE_PROVIDER", setting)
    text = code_cli.code_provider_setting_error()
    assert text.startswith(f"FACTORY_CODE_PROVIDER={setting} is not a provider")
    assert all(name in text for name in PROVIDER_TABLE)


@pytest.mark.parametrize("setting", ["", "anthropic"])
def test_code_provider_setting_error_is_empty_for_unset_or_a_table_name(monkeypatch, setting):
    _clean(monkeypatch)
    monkeypatch.setenv("FACTORY_CODE_PROVIDER", setting)
    assert code_cli.code_provider_setting_error() == ""


@pytest.mark.parametrize(
    "cli, key, expected",
    [("/opt/kimi", False, False), ("/usr/bin/claude", True, True), ("/opt/coder", True, True)],
)
def test_deepseek_coder_selected_follows_the_key_not_a_binary_name(
    monkeypatch, cli, key, expected
):
    _clean(monkeypatch)
    if key:
        monkeypatch.setenv("DEEPSEEK_API_KEY", _key())
    assert code_cli.deepseek_coder_selected(cli) is expected


@pytest.mark.parametrize("default, expected", [(None, ""), ("/opt/given", "/opt/given")])
def test_code_cli_command_assumes_no_binary(monkeypatch, default, expected):
    _clean(monkeypatch)
    assert code_cli.code_cli_command(default) == expected


@pytest.mark.parametrize("cli, expected", [("/usr/bin/claude", ""), ("/opt/coder", "/opt/coder")])
def test_code_cli_command_never_hands_deepseek_to_claude_code(monkeypatch, cli, expected):
    _clean(monkeypatch)
    monkeypatch.setenv("DEEPSEEK_API_KEY", _key())
    monkeypatch.setenv("FACTORY_CODE_CLI", cli)
    assert code_cli.code_cli_command() == expected


@pytest.mark.parametrize("name", ["DEFAULT_CODE_CLI", "DEFAULT_DEEPSEEK_CLI"])
def test_the_module_defaults_name_no_binary(name):
    assert getattr(code_cli, name) == ""


# -- coder_session -----------------------------------------------------------


@pytest.mark.parametrize(
    "provider, key, expected",
    [("", False, True), ("openrouter", False, False), ("", True, False)],
)
def test_cli_requires_kimi_credentials_means_outside_the_table(
    monkeypatch, provider, key, expected
):
    _clean(monkeypatch)
    if provider:
        monkeypatch.setenv("FACTORY_CODE_PROVIDER", provider)
    if key:
        monkeypatch.setenv("DEEPSEEK_API_KEY", _key())
    assert coder_session.cli_requires_kimi_credentials("/opt/coder") is expected


@pytest.mark.parametrize(
    "setting",
    ["", *sorted(PROVIDER_TABLE), "anthropic", "DeepSeek", " deepseek ", "kimi", "qwen"],
)
@pytest.mark.parametrize("key", [False, True])
@pytest.mark.parametrize("env_cli", ["", "/usr/bin/claude", "/opt/coder"])
def test_deepseek_selected_always_implies_a_table_provider(
    monkeypatch, setting, key, env_cli
):
    """The behaviour CI-M-a's ``drop_rule`` survivor is judged against: for
    every input, DeepSeek selected implies a non-empty table provider, so
    ``cli_requires_kimi_credentials`` equals ``not selected and not provider``
    and equals ``not provider`` alone. Both forms are pinned, over every
    table name, unknown names, key present or absent, and claude or other
    commands (argument and setting)."""
    _clean(monkeypatch)
    if setting:
        monkeypatch.setenv("FACTORY_CODE_PROVIDER", setting)
    if key:
        monkeypatch.setenv("DEEPSEEK_API_KEY", _key())
    if env_cli:
        monkeypatch.setenv("FACTORY_CODE_CLI", env_cli)
    for command in (None, "", "/usr/bin/claude", "/opt/coder"):
        selected = code_cli.deepseek_coder_selected(command)
        provider = code_cli.factory_code_provider()
        assert not selected or provider, (setting, key, env_cli, command)
        required = coder_session.cli_requires_kimi_credentials(command)
        assert required is (not selected and not provider)
        assert required is (not provider)


@pytest.mark.parametrize(
    "provider, key, expected",
    [("", False, False), ("claude", False, True), ("deepseek", False, False), ("deepseek", True, True)],
)
def test_cli_credentials_ok_only_through_the_table(monkeypatch, provider, key, expected):
    _clean(monkeypatch)
    if provider:
        monkeypatch.setenv("FACTORY_CODE_PROVIDER", provider)
    if key:
        monkeypatch.setenv("DEEPSEEK_API_KEY", _key())
    assert coder_session.cli_credentials_ok("/opt/coder") is expected


@pytest.mark.parametrize("setting, refused", [("", False), ("moonshot", True)])
def test_table_provider_credentials_names_settings_and_a_refusal(monkeypatch, setting, refused):
    _clean(monkeypatch)
    monkeypatch.setenv("FACTORY_CODE_PROVIDER", setting)
    text = coder_session._table_provider_credentials()
    assert text.startswith("DEEPSEEK_API_KEY for DeepSeek, or FACTORY_CODE_PROVIDER set to one of ")
    assert ("is not a provider in the shared table" in text) is refused


@pytest.mark.parametrize(
    "command, expected",
    [("", "no coding CLI is set (neither FACTORY_CODE_CLI nor KIMI_CODE_CLI)"),
     ("/opt/missing", "'/opt/missing' is not an executable on this host")],
)
def test_cli_unavailable_detail_names_what_is_missing(monkeypatch, command, expected):
    _clean(monkeypatch)
    text = coder_session.cli_unavailable_detail(command)
    assert text.startswith(f"{coder_session.NAMED_BLOCKER_CLI}: {expected}. ")
    assert "FACTORY_CODE_PROVIDER set to one of" in text


@pytest.mark.parametrize(
    "key, needle",
    [(True, "no DeepSeek coding model is configured"),
     (False, "no coding model is configured for it")],
)
def test_cli_no_model_detail_per_branch(monkeypatch, key, needle):
    _clean(monkeypatch)
    if key:
        monkeypatch.setenv("DEEPSEEK_API_KEY", _key())
    text = coder_session.cli_no_model_detail("/opt/coder")
    assert text.startswith(f"{coder_session.NAMED_BLOCKER_CLI_NO_MODEL}: '/opt/coder' is an executable")
    assert needle in text


@pytest.mark.parametrize(
    "provider, needle",
    [("deepseek", "DEEPSEEK_API_KEY is unset"),
     ("", "no provider from the shared table is configured for it")],
)
def test_cli_credentials_missing_detail_per_branch(monkeypatch, provider, needle):
    _clean(monkeypatch)
    if provider:
        monkeypatch.setenv("FACTORY_CODE_PROVIDER", provider)
    text = coder_session.cli_credentials_missing_detail("/opt/coder")
    assert text.startswith(f"{coder_session.NAMED_BLOCKER_CLI_CREDS}: '/opt/coder' is an executable")
    assert needle in text


@pytest.mark.parametrize(
    "text, expected",
    [(coder_session._provider_block("k", "u"), True),
     ('[providers.deepseek]\ntype = "openai"\n', False)],
)
def test_config_has_kimi_provider_reads_the_table_the_writer_writes(text, expected):
    assert coder_session.config_has_kimi_provider(text) is expected


@pytest.mark.parametrize("cli", ["/opt/coder-a", "/opt/coder-b"])
def test_wire_deepseek_cli_credentials_reports_the_configured_cli(monkeypatch, tmp_path, cli):
    _clean(monkeypatch)
    monkeypatch.setenv("KIMI_CODE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("FACTORY_CODE_CLI", cli)
    monkeypatch.setenv("DEEPSEEK_API_KEY", _key())
    wired = coder_session._wire_deepseek_cli_credentials()
    assert (wired["ok"], wired["provider"], wired["cli"]) == (True, "deepseek", cli)
    monkeypatch.setenv("FACTORY_CODE_PROVIDER", "openrouter")
    kept = coder_session._wire_deepseek_cli_credentials()
    assert (kept["wrote"], kept["provider"], kept["cli"]) == (False, "openrouter", cli)
    assert kept["reason"] == "deepseek key present but FACTORY_CODE_PROVIDER=openrouter"


@pytest.mark.parametrize("provider", ["openrouter", ""])
def test_merge_cli_credential_result_reports_the_table_provider(monkeypatch, provider):
    _clean(monkeypatch)
    if provider:
        monkeypatch.setenv("FACTORY_CODE_PROVIDER", provider)
    merged = coder_session._merge_cli_credential_result({"ok": True}, {"ok": False})
    assert merged["provider"] == provider
    assert merged["deepseek"] == {"ok": False}
    lifted = coder_session._merge_cli_credential_result({"ok": False}, {"ok": True, "reason": "r"})
    assert (lifted["ok"], lifted["provider"], lifted["reason"]) == (True, "deepseek", "r")


# -- the admin coder picker ---------------------------------------------------


@pytest.fixture
def admin(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    monkeypatch.setenv("CEREBRUM_DEV_API_KEY", "master-" + secrets.token_hex(6))
    import app.core.runtime_settings as rs

    importlib.reload(rs)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.routers import admin_ops

    app = FastAPI()
    app.include_router(admin_ops.router, prefix="/v1/admin")
    headers = {"Authorization": "Bearer " + __import__("os").environ["CEREBRUM_DEV_API_KEY"]}
    return TestClient(app), headers, admin_ops


@pytest.mark.parametrize("value, expected", [("", ""), ("anthropic", "")])
def test_provider_refusal_accepts_blank_or_a_table_name(admin, value, expected):
    assert admin[2]._provider_refusal(value) == expected


@pytest.mark.parametrize("retired", _RETIRED)
def test_provider_refusal_names_a_retired_vendor(admin, retired):
    assert admin[2]._provider_refusal(retired) == (
        f"provider {retired!r} is not in the shared provider table; "
        f"choose one of {', '.join(sorted(PROVIDER_TABLE))}"
    )


@pytest.mark.parametrize("retired", _RETIRED)
def test_set_coder_model_refuses_a_retired_provider(admin, retired):
    client, headers, _ = admin
    r = client.post("/v1/admin/coder-model", json={"provider": retired}, headers=headers)
    assert r.status_code == 422, r.text
    assert retired in r.json()["detail"]
    g = client.get("/v1/admin/coder-model", headers=headers)
    assert "coder_provider" not in g.json()["overrides"]


@pytest.mark.parametrize("provider", ["openrouter", "anthropic"])
def test_set_coder_model_keeps_a_table_provider_as_typed(admin, provider):
    client, headers, _ = admin
    r = client.post("/v1/admin/coder-model", json={"provider": provider}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["overrides"]["coder_provider"] == provider
    g = client.get("/v1/admin/coder-model", headers=headers)
    assert g.json()["provider_options"] == sorted(PROVIDER_TABLE)
    assert not set(_RETIRED) & set(g.json()["provider_options"])
