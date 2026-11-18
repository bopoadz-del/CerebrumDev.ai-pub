"""Item 1c-3: the headless worker's provider is checked against the ONE table.

``worker_provider()`` used to return the admin override or the
``CODEWHALE_PROVIDER`` setting as typed. A value outside the shared provider
table (``app.core.llm_config.PROVIDER_TABLE``) now selects no provider, never
falls through to the env or the default, and the job is refused naming the
setting, through the same helper the code-CLI path refuses through
(``code_cli.provider_setting_refusal``). Two inputs for every branch, and the
negative for each.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from app.factory import code_cli
from app.factory.build import codewhale_worker
from app.factory.build.codewhale_worker import (
    WORKER_PROVIDER_REFUSED,
    WorkerError,
    run_worker_job,
)
from app.factory.build.tenant_bind import TenantStoreBinding

_ENV = (
    "CODEWHALE_PROVIDER",
    "CODEWHALE_API_KEY",
    "CEREBRUM_CHAT_LLM_API_KEY",
    "DEEPSEEK_API_KEY",
    "OPENROUTER_API_KEY",
    "CLAUDE_API_KEY",
    "FACTORY_CODE_PROVIDER",
)


@pytest.fixture
def rs(tmp_path, monkeypatch):
    """Runtime settings on an empty tmp store, and a clean provider env."""
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    for key in _ENV:
        monkeypatch.delenv(key, raising=False)
    import app.core.runtime_settings as runtime_settings

    importlib.reload(runtime_settings)
    return runtime_settings


# --- worker_provider: a table name or nothing --------------------------------


@pytest.mark.parametrize(
    "typed, expected",
    [("openrouter", "openrouter"), ("Anthropic", "claude"), ("cursor", "cursor")],
)
def test_a_table_env_setting_is_the_provider(rs, monkeypatch, typed, expected):
    monkeypatch.setenv("CODEWHALE_PROVIDER", typed)
    assert codewhale_worker.worker_provider() == expected
    assert codewhale_worker.worker_provider_setting_error() == ""


@pytest.mark.parametrize("typed", ["acme", "not-a-provider"])
def test_an_env_setting_outside_the_table_selects_no_provider(rs, monkeypatch, typed):
    monkeypatch.setenv("CODEWHALE_PROVIDER", typed)
    assert codewhale_worker.worker_provider() == ""
    refusal = codewhale_worker.worker_provider_setting_error()
    assert f"CODEWHALE_PROVIDER={typed}" in refusal
    assert "not a provider in the shared table" in refusal


def test_nothing_set_is_the_default_table_name(rs):
    assert codewhale_worker.worker_provider() == codewhale_worker.WORKER_PROVIDER_DEFAULT
    assert codewhale_worker.worker_provider_setting_error() == ""


@pytest.mark.parametrize("override, env", [("acme", "openrouter"), ("zzz", "")])
def test_an_admin_override_outside_the_table_never_falls_through(
    rs, monkeypatch, override, env
):
    # The override wins; outside the table it selects NOTHING, not the env
    # setting and not the default.
    if env:
        monkeypatch.setenv("CODEWHALE_PROVIDER", env)
    rs.set_value(rs.CODER_PROVIDER, override)
    assert codewhale_worker.worker_provider() == ""
    refusal = codewhale_worker.worker_provider_setting_error()
    assert f"{codewhale_worker.WORKER_PROVIDER_ADMIN_SETTING}={override}" in refusal


@pytest.mark.parametrize("override, env", [("claude", "openrouter"), ("openrouter", "acme")])
def test_a_table_admin_override_wins_over_the_env(rs, monkeypatch, override, env):
    monkeypatch.setenv("CODEWHALE_PROVIDER", env)
    rs.set_value(rs.CODER_PROVIDER, override)
    assert codewhale_worker.worker_provider() == override
    assert codewhale_worker.worker_provider_setting_error() == ""


# --- the same helper the code-CLI path uses ----------------------------------


@pytest.mark.parametrize("setting, value", [("A_SETTING", "acme"), ("B", " Nope ")])
def test_the_shared_refusal_names_the_setting_and_the_table(setting, value):
    from app.core.llm_config import PROVIDER_TABLE

    text = code_cli.provider_setting_refusal(setting, value)
    assert text.startswith(f"{setting}={value.strip()} ")
    for name in PROVIDER_TABLE:
        assert name in text


@pytest.mark.parametrize("value", ["", "  ", "deepseek", "ANTHROPIC"])
def test_the_shared_refusal_is_empty_for_blank_or_a_table_name(value):
    assert code_cli.provider_setting_refusal("ANY", value) == ""


@pytest.mark.parametrize("marker", ["sentinel-one", "sentinel-two"])
def test_worker_and_code_cli_refuse_through_one_helper(rs, monkeypatch, marker):
    seen = []

    def _fake(setting, value):
        seen.append((setting, value))
        return marker

    monkeypatch.setattr(code_cli, "provider_setting_refusal", _fake)
    monkeypatch.setenv("CODEWHALE_PROVIDER", "acme")
    monkeypatch.setenv("FACTORY_CODE_PROVIDER", "acme")
    assert codewhale_worker.worker_provider_setting_error() == marker
    assert code_cli.code_provider_setting_error() == marker
    assert seen == [("CODEWHALE_PROVIDER", "acme"), ("FACTORY_CODE_PROVIDER", "acme")]


# --- the child env never stamps a key for a refused provider ------------------


def test_child_env_stamps_no_provider_var_for_a_refused_provider(rs, monkeypatch):
    monkeypatch.setenv("CODEWHALE_PROVIDER", "acme")
    monkeypatch.setenv("CEREBRUM_CHAT_LLM_API_KEY", "sk-valid")
    env = codewhale_worker._child_env(session_id="sess_x")
    assert env["CODEWHALE_API_KEY"] == "sk-valid"
    assert "_API_KEY" not in env
    assert "ACME_API_KEY" not in env
    assert "DEEPSEEK_API_KEY" not in env


@pytest.mark.parametrize("typed", ["", "DeepSeek"])
def test_child_env_stamps_the_table_provider_var(rs, monkeypatch, typed):
    # The default and a typed table name (normalised) both stamp the
    # provider's own key var. (The confinement withholds every other table
    # provider's key var from the child, so DeepSeek is the one that shows.)
    if typed:
        monkeypatch.setenv("CODEWHALE_PROVIDER", typed)
    monkeypatch.setenv("CEREBRUM_CHAT_LLM_API_KEY", "sk-valid")
    env = codewhale_worker._child_env(session_id="sess_x")
    assert env["DEEPSEEK_API_KEY"] == "sk-valid"
    assert "_API_KEY" not in env


# --- the job is refused by name before the CLI starts -------------------------


class _PastTheRefusal(Exception):
    pass


def _binding() -> TenantStoreBinding:
    return TenantStoreBinding(
        tenant_key="key_1c3", store_dir=Path("/tmp/tenant-1c3"), bound_at=0.0
    )


def _arm(monkeypatch):
    monkeypatch.setattr(codewhale_worker, "worker_cli_path", lambda: "/usr/local/bin/codewhale")

    def _slot(*_a, **_k):
        raise _PastTheRefusal()

    monkeypatch.setattr(codewhale_worker, "worker_job_slot", _slot)


@pytest.mark.parametrize("typed", ["acme", "retired-or-typo"])
def test_a_job_with_a_provider_outside_the_table_is_refused_by_name(
    rs, monkeypatch, tmp_path, typed
):
    _arm(monkeypatch)
    monkeypatch.setenv("CODEWHALE_PROVIDER", typed)
    with pytest.raises(WorkerError) as err:
        run_worker_job("build it", tmp_path / "co", tenant_store=_binding())
    text = str(err.value)
    assert text.startswith(f"{WORKER_PROVIDER_REFUSED}: ")
    assert f"CODEWHALE_PROVIDER={typed}" in text


@pytest.mark.parametrize("typed", ["openrouter", ""])
def test_a_job_with_a_table_provider_goes_past_the_check(rs, monkeypatch, tmp_path, typed):
    _arm(monkeypatch)
    if typed:
        monkeypatch.setenv("CODEWHALE_PROVIDER", typed)
    with pytest.raises(_PastTheRefusal):
        run_worker_job("build it", tmp_path / "co", tenant_store=_binding())
