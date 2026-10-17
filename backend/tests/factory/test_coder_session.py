"""One-session FACTORY_CODE_CLI dispatch + owner Pause/Stop."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from app.factory.build.authority import BuildRole
from app.factory.build.brief_compiler import compile_brief
from app.factory.build.coder_session import (
    CLI_AUTH_BILLING_BLOCKERS,
    CLI_PREFLIGHT_BLOCKERS,
    CONTROL_PAUSE,
    CONTROL_STOP,
    DEFAULT_KIMI_CODE_MODEL,
    DEFAULT_KIMI_CODE_MODEL_ID,
    KEEP_PATH_FACTORY_GROUNDED_REUSE,
    should_factory_grounded_generate_persist,
    should_factory_llm_generate_gaps,
    should_keep_factory_grounded_reuse,
    NAMED_BLOCKER_CLI,
    NAMED_BLOCKER_CLI_BILLING,
    NAMED_BLOCKER_CLI_CREDS,
    NAMED_BLOCKER_CLI_FAILED,
    NAMED_BLOCKER_CLI_MODEL_DENIED,
    NAMED_BLOCKER_CLI_NO_MODEL,
    NAMED_BLOCKER_STOPPED,
    CodeCliBillingFailed,
    CodeCliCredentialsMissing,
    CodeCliFailed,
    CodeCliModelDenied,
    CodeCliNoModelConfigured,
    CodeCliUnavailable,
    DispatchResult,
    _has_brief_workflow_steps,
    _is_keepable_handler,
    _merge_workspace_harvest,
    brief_dispatch_enabled,
    brief_requires_cli,
    classify_cli_exit,
    cli_available,
    config_default_model,
    config_has_usable_default_model,
    config_model_id,
    cli_credentials_ok,
    cli_default_model_ok,
    cli_unavailable_detail,
    dispatch_compiled_brief,
    ensure_code_cli_credentials,
    harvest_cli_artifacts,
    harvest_unkeepable_event_bus_ids,
    http_oneshot_enabled,
    inventory_gap_ids,
    probe_code_cli,
    raise_if_cli_session_unready,
    read_control,
    read_log_tail,
    resolve_code_cli,
    session_status,
    specs_from_models_source,
    wait_if_paused,
    write_control,
)
from app.factory.build.roles_models import RoleContext
from app.factory.build.workspace import RoleWorkspace


class _Cap:
    capability_id = "analytics_surface"
    block_ids = ("analytics",)
    strategy = "REUSE"
    notes = "agg"


class _Plan:
    capabilities = (_Cap(),)


class _Blueprint:
    product_name = "Smoke"
    product_id = "runner-smoke"
    vertical = "product"
    summary = "smoke"


def _ctx(tmp_path: Path) -> RoleContext:
    ws = RoleWorkspace(BuildRole.WRITER, tmp_path / "build")
    return RoleContext(
        role=BuildRole.WRITER,
        workspace=ws,
        blueprint=_Blueprint(),
        plan=_Plan(),
        state={},
    )


def test_brief_dispatch_defaults_on(monkeypatch):
    monkeypatch.delenv("FACTORY_BRIEF_DISPATCH", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("FACTORY_CODE_PROVIDER", raising=False)
    assert brief_dispatch_enabled() is True
    monkeypatch.setenv("FACTORY_BRIEF_DISPATCH", "0")
    assert brief_dispatch_enabled() is False


def test_pause_stop_resume_control_file(tmp_path):
    root = tmp_path / "ws"
    write_control(root, "pause")
    assert read_control(root) == CONTROL_PAUSE
    write_control(root, "resume")
    assert read_control(root) == "run"
    write_control(root, "stop")
    assert read_control(root) == CONTROL_STOP


def test_wait_if_paused_returns_stop_on_deadline(tmp_path):
    root = tmp_path / "ws"
    write_control(root, "pause")
    clock = {"t": 0.0}

    def now():
        return clock["t"]

    action = wait_if_paused(
        root,
        clock=now,
        sleep=lambda _s: clock.__setitem__("t", clock["t"] + 1),
        poll_s=0.01,
        deadline=0.5,
    )
    assert action == CONTROL_STOP


def test_dispatch_without_cli_names_the_blocker(tmp_path, monkeypatch):
    """CLI missing + coder on → named class; HTTP oneshot is not used."""
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "1")
    monkeypatch.delenv("FACTORY_BRIEF_HTTP_ONESHOT", raising=False)
    monkeypatch.setenv("FACTORY_CODE_CLI", "/definitely/not/a/cli")
    monkeypatch.setattr(
        "app.factory.build.coder_session.cli_available",
        lambda command=None: False,
    )
    oneshot = []
    monkeypatch.setattr(
        "app.factory.coder.generate_from_compiled_brief",
        lambda **kw: oneshot.append(kw) or {"specs": {}, "handlers": {}, "model": "x"},
    )
    ctx = _ctx(tmp_path)
    compiled = compile_brief(ctx.blueprint, ctx.plan, store_ids={"analytics"})
    ctx.workspace.write_text(Path("docs") / "coder_brief.md", compiled.text)
    ctx.workspace.write_text(Path("docs") / "coder_session.log", "")
    result = dispatch_compiled_brief(ctx, compiled)
    assert result.ok is False
    assert result.via == "unavailable"
    assert result.blocker == NAMED_BLOCKER_CLI
    assert NAMED_BLOCKER_CLI in result.detail
    assert "FACTORY_CODE_CLI" in result.detail
    assert oneshot == [], "HTTP oneshot must not run when CLI is missing"
    receipt = json.loads(
        (tmp_path / "build" / "docs" / "coder_receipt.json").read_text(encoding="utf-8")
    )
    assert receipt["blocker"] == NAMED_BLOCKER_CLI
    status = session_status(tmp_path / "build")
    assert status["coder_receipt"]["blocker"] == NAMED_BLOCKER_CLI
    log = read_log_tail(tmp_path / "build")
    assert NAMED_BLOCKER_CLI in log
    assert "falling back to HTTP oneshot" not in log


def test_dispatch_with_cli_does_not_use_oneshot(tmp_path, monkeypatch):
    script = tmp_path / "fake_coder.sh"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setenv("FACTORY_CODE_CLI", str(script))
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "1")
    oneshot = []
    monkeypatch.setattr(
        "app.factory.coder.generate_from_compiled_brief",
        lambda **kw: oneshot.append(kw) or {"specs": {}, "handlers": {}, "model": "x"},
    )
    ctx = _ctx(tmp_path)
    compiled = compile_brief(ctx.blueprint, ctx.plan, store_ids={"analytics"})
    ctx.workspace.write_text(Path("docs") / "coder_brief.md", compiled.text)
    ctx.workspace.write_text(Path("docs") / "coder_session.log", "")
    result = dispatch_compiled_brief(ctx, compiled)
    assert result.ok, result.detail
    assert result.via == "cli"
    assert oneshot == [], "CLI present must not call HTTP oneshot"


def test_dispatch_deadline_tracks_7200s_timeout_not_1785(tmp_path, monkeypatch):
    """sess_2fba31ab: Floor deadline_s must follow FACTORY_CODER_TIMEOUT_S."""
    from app.factory.llm_watchdog import MODEL_CALL_GRACE_S

    script = tmp_path / "fake_coder.sh"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setenv("FACTORY_CODE_CLI", str(script))
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "1")
    monkeypatch.setenv("FACTORY_CODER_TIMEOUT_S", "7200")
    monkeypatch.delenv("FACTORY_CODER_ATTEMPT_WALL_S", raising=False)
    monkeypatch.delenv("FACTORY_CODER_BUDGET_S", raising=False)
    notes: list = []
    ctx = _ctx(tmp_path)
    started = time.monotonic()
    ctx.deadline = started + 1800.0
    ctx.deadline_box = {"at": started + 1800.0, "clock": time.monotonic}
    ctx.progress = lambda detail, payload: notes.append((detail, dict(payload)))
    compiled = compile_brief(ctx.blueprint, ctx.plan, store_ids={"analytics"})
    ctx.workspace.write_text(Path("docs") / "coder_brief.md", compiled.text)
    ctx.workspace.write_text(Path("docs") / "coder_session.log", "")
    result = dispatch_compiled_brief(ctx, compiled)
    assert result.ok, result.detail
    dispatch = [
        payload
        for detail, payload in notes
        if payload.get("model_call") and "dispatching compiled brief" in detail
    ]
    assert dispatch, notes
    deadline = float(dispatch[0]["deadline_s"])
    assert deadline == pytest.approx(7200.0 + MODEL_CALL_GRACE_S)
    assert deadline != 1785.0
    assert deadline != pytest.approx(1800.0 - 15.0)


def test_brief_requires_cli_when_coder_on(monkeypatch):
    from app.core.llm_config import CURSOR_KEY_ENVS

    monkeypatch.setenv("FACTORY_CODER_ENABLED", "1")
    monkeypatch.delenv("FACTORY_BRIEF_HTTP_ONESHOT", raising=False)
    monkeypatch.delenv("FACTORY_BRIEF_DISPATCH", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("FACTORY_CODE_PROVIDER", raising=False)
    for name in CURSOR_KEY_ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("FACTORY_BRIEF_REQUIRE_CLI", "1")
    assert brief_requires_cli() is True
    assert http_oneshot_enabled() is False
    monkeypatch.setenv("FACTORY_BRIEF_HTTP_ONESHOT", "1")
    assert brief_requires_cli() is False
    monkeypatch.delenv("FACTORY_BRIEF_HTTP_ONESHOT", raising=False)
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")
    assert brief_requires_cli() is False
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "1")
    monkeypatch.delenv("FACTORY_BRIEF_REQUIRE_CLI", raising=False)
    monkeypatch.setenv("ENV", "test")
    assert brief_requires_cli() is False
    monkeypatch.setenv("ENV", "production")
    assert brief_requires_cli() is True


def test_cli_unavailable_detail_names_env():
    text = cli_unavailable_detail("/no/such/coder")
    assert NAMED_BLOCKER_CLI in text
    assert "FACTORY_CODE_CLI" in text
    assert "KIMI_CODE_API_KEY" in text
    assert "FACTORY_BRIEF_HTTP_ONESHOT" in text


def test_probe_code_cli_reports_unavailable(monkeypatch):
    from app.core.llm_config import CURSOR_KEY_ENVS

    monkeypatch.setenv("FACTORY_CODE_CLI", "/no/such/coder")
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "1")
    monkeypatch.setenv("FACTORY_BRIEF_REQUIRE_CLI", "1")
    monkeypatch.delenv("FACTORY_BRIEF_HTTP_ONESHOT", raising=False)
    for name in CURSOR_KEY_ENVS:
        monkeypatch.delenv(name, raising=False)
    probe = probe_code_cli()
    assert probe["available"] is False
    assert probe["blocker"] == NAMED_BLOCKER_CLI
    assert probe["requires_cli"] is True
    assert probe["cursor_ba_available"] is False


def test_resolve_code_cli_finds_home_local_bin(tmp_path, monkeypatch):
    bindir = tmp_path / ".local" / "bin"
    bindir.mkdir(parents=True)
    script = bindir / "kimi"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("FACTORY_CODE_CLI", "kimi")
    monkeypatch.delenv("KIMI_CODE_CLI", raising=False)
    # Not on PATH — only the extra home location.
    monkeypatch.setenv("PATH", "/usr/bin")
    assert resolve_code_cli() == str(script)
    assert cli_available() is True


def test_ensure_code_cli_credentials_writes_when_key_set(tmp_path, monkeypatch):
    monkeypatch.setenv("KIMI_CODE_HOME", str(tmp_path / "kimi-home"))
    monkeypatch.setenv("KIMI_CODE_API_KEY", "sk-test-not-real")
    monkeypatch.delenv("KIMI_CODE_MODEL", raising=False)
    monkeypatch.delenv("KIMI_CODE_MODEL_ID", raising=False)
    result = ensure_code_cli_credentials()
    assert result["wrote"] is True
    assert result.get("mutated") is False
    assert result["model"] == DEFAULT_KIMI_CODE_MODEL
    assert result["model_id"] == DEFAULT_KIMI_CODE_MODEL_ID
    text = Path(result["path"]).read_text(encoding="utf-8")
    assert "[providers.kimi]" in text
    assert "sk-test-not-real" in text
    assert f'default_model = "{DEFAULT_KIMI_CODE_MODEL}"' in text
    assert f'[models."{DEFAULT_KIMI_CODE_MODEL}"]' in text
    assert config_default_model(text) == "kimi-k3"
    assert config_model_id(text, "kimi-k3") == "kimi-k3"
    assert 'model = "kimi-k3"' in text
    assert "max_context_size = 1048576" in text
    assert 'model = "k3"' not in text
    assert "kimi-code/k3" not in text


def test_ensure_code_cli_credentials_honours_kimi_code_model(tmp_path, monkeypatch):
    monkeypatch.setenv("KIMI_CODE_HOME", str(tmp_path / "kimi-home"))
    monkeypatch.setenv("KIMI_CODE_API_KEY", "sk-test-not-real")
    monkeypatch.setenv("KIMI_CODE_MODEL", "kimi-k2.7-code")
    monkeypatch.delenv("KIMI_CODE_MODEL_ID", raising=False)
    result = ensure_code_cli_credentials()
    assert result["wrote"] is True
    assert result["model"] == "kimi-k2.7-code"
    assert result["model_id"] == "kimi-k2.7-code"
    text = Path(result["path"]).read_text(encoding="utf-8")
    assert 'default_model = "kimi-k2.7-code"' in text
    assert '[models."kimi-k2.7-code"]' in text
    assert config_model_id(text, "kimi-k2.7-code") == "kimi-k2.7-code"
    assert 'model = "kimi-k2.7-code"' in text
    assert "max_context_size = 262144" in text


def test_ensure_code_cli_credentials_honours_kimi_code_model_id(tmp_path, monkeypatch):
    monkeypatch.setenv("KIMI_CODE_HOME", str(tmp_path / "kimi-home"))
    monkeypatch.setenv("KIMI_CODE_API_KEY", "sk-test-not-real")
    monkeypatch.setenv("KIMI_CODE_MODEL", "kimi-k2.7-code-highspeed")
    monkeypatch.setenv("KIMI_CODE_MODEL_ID", "kimi-k2.7-code-highspeed")
    result = ensure_code_cli_credentials()
    text = Path(result["path"]).read_text(encoding="utf-8")
    assert config_default_model(text) == "kimi-k2.7-code-highspeed"
    assert config_model_id(text, "kimi-k2.7-code-highspeed") == "kimi-k2.7-code-highspeed"
    assert result["model_id"] == "kimi-k2.7-code-highspeed"


def test_ensure_code_cli_credentials_maps_managed_k3_env(tmp_path, monkeypatch):
    """KIMI_CODE_MODEL=kimi-code/k3 (docs leftover) must write Moonshot kimi-k3."""
    monkeypatch.setenv("KIMI_CODE_HOME", str(tmp_path / "kimi-home"))
    monkeypatch.setenv("KIMI_CODE_API_KEY", "sk-test-not-real")
    monkeypatch.setenv("KIMI_CODE_MODEL", "kimi-code/k3")
    monkeypatch.delenv("KIMI_CODE_MODEL_ID", raising=False)
    result = ensure_code_cli_credentials()
    text = Path(result["path"]).read_text(encoding="utf-8")
    assert result["model"] == "kimi-k3"
    assert result["model_id"] == "kimi-k3"
    assert config_default_model(text) == "kimi-k3"
    assert config_model_id(text, "kimi-k3") == "kimi-k3"
    assert "kimi-code/k3" not in text
    assert 'model = "k3"' not in text


def test_ensure_code_cli_credentials_mutates_when_model_missing(tmp_path, monkeypatch):
    """Live-shaped file: [providers.kimi] present, default_model absent."""
    home = tmp_path / "kimi-home"
    home.mkdir()
    dest = home / "config.toml"
    dest.write_text(
        "[providers.kimi]\n"
        'type = "kimi"\n'
        'api_key = "sk-test-not-real"\n'
        'base_url = "https://api.moonshot.ai/v1"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("KIMI_CODE_HOME", str(home))
    monkeypatch.setenv("KIMI_CODE_API_KEY", "sk-test-not-real")
    monkeypatch.delenv("KIMI_CODE_MODEL", raising=False)
    monkeypatch.delenv("KIMI_CODE_MODEL_ID", raising=False)
    result = ensure_code_cli_credentials()
    assert result["ok"] is True
    assert result["wrote"] is False
    assert result["mutated"] is True
    assert result["model"] == DEFAULT_KIMI_CODE_MODEL
    assert result["model_id"] == DEFAULT_KIMI_CODE_MODEL_ID
    text = dest.read_text(encoding="utf-8")
    assert f'default_model = "{DEFAULT_KIMI_CODE_MODEL}"' in text
    assert f'[models."{DEFAULT_KIMI_CODE_MODEL}"]' in text
    assert config_model_id(text, DEFAULT_KIMI_CODE_MODEL) == DEFAULT_KIMI_CODE_MODEL_ID
    assert "[providers.kimi]" in text
    assert "sk-test-not-real" in text
    again = ensure_code_cli_credentials()
    assert again["wrote"] is False
    assert again["mutated"] is False
    assert again["reason"] == "already present"


def test_ensure_code_cli_credentials_rewrites_managed_k3_file(tmp_path, monkeypatch):
    """#324 live shape: default_model=kimi-code/k3 / model=k3 on Moonshot."""
    home = tmp_path / "kimi-home"
    home.mkdir()
    dest = home / "config.toml"
    dest.write_text(
        'default_model = "kimi-code/k3"\n'
        "\n"
        "[providers.kimi]\n"
        'type = "kimi"\n'
        'api_key = "sk-test-not-real"\n'
        'base_url = "https://api.moonshot.ai/v1"\n'
        "\n"
        '[models."kimi-code/k3"]\n'
        'provider = "kimi"\n'
        'model = "k3"\n'
        "max_context_size = 1048576\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("KIMI_CODE_HOME", str(home))
    monkeypatch.setenv("KIMI_CODE_API_KEY", "sk-test-not-real")
    monkeypatch.delenv("KIMI_CODE_MODEL", raising=False)
    monkeypatch.delenv("KIMI_CODE_MODEL_ID", raising=False)
    result = ensure_code_cli_credentials()
    assert result["ok"] is True
    assert result["wrote"] is False
    assert result["mutated"] is True
    assert result["model"] == "kimi-k3"
    assert result["model_id"] == "kimi-k3"
    text = dest.read_text(encoding="utf-8")
    assert config_default_model(text) == "kimi-k3"
    assert config_model_id(text, "kimi-k3") == "kimi-k3"
    assert 'default_model = "kimi-k3"' in text
    assert '[models."kimi-k3"]' in text
    assert 'model = "kimi-k3"' in text
    assert "kimi-code/k3" not in text
    assert 'model = "k3"' not in text
    assert "sk-test-not-real" in text
    again = ensure_code_cli_credentials()
    assert again["mutated"] is False
    assert again["reason"] == "already present"


def test_ensure_code_cli_credentials_skips_when_unset(tmp_path, monkeypatch):
    monkeypatch.setenv("KIMI_CODE_HOME", str(tmp_path / "kimi-home"))
    monkeypatch.delenv("KIMI_CODE_API_KEY", raising=False)
    monkeypatch.delenv("KIMI_CODE_KEY", raising=False)
    result = ensure_code_cli_credentials()
    assert result["wrote"] is False
    assert "unset" in result["reason"]


def test_ensure_code_cli_credentials_repairs_model_when_key_unset(
    tmp_path, monkeypatch
):
    """Credentials-only file must gain default_model on next start."""
    home = tmp_path / "kimi-home"
    home.mkdir()
    dest = home / "config.toml"
    dest.write_text(_credentials_only_toml(), encoding="utf-8")
    monkeypatch.setenv("KIMI_CODE_HOME", str(home))
    monkeypatch.delenv("KIMI_CODE_API_KEY", raising=False)
    monkeypatch.delenv("KIMI_CODE_KEY", raising=False)
    monkeypatch.delenv("KIMI_CODE_MODEL", raising=False)
    monkeypatch.delenv("KIMI_CODE_MODEL_ID", raising=False)
    result = ensure_code_cli_credentials()
    assert result["ok"] is True
    assert result["wrote"] is False
    assert result["mutated"] is True
    assert result["model"] == DEFAULT_KIMI_CODE_MODEL
    text = dest.read_text(encoding="utf-8")
    assert f'default_model = "{DEFAULT_KIMI_CODE_MODEL}"' in text
    assert f'[models."{DEFAULT_KIMI_CODE_MODEL}"]' in text
    assert "sk-test-not-real" in text
    assert config_has_usable_default_model(text) is True


def test_config_has_usable_default_model_rejects_credentials_only():
    assert config_has_usable_default_model(_credentials_only_toml()) is False
    assert config_has_usable_default_model("") is False
    assert config_has_usable_default_model('default_model = ""\n') is False
    assert config_has_usable_default_model(_usable_kimi_toml()) is True
    managed = (
        'default_model = "kimi-code/k3"\n'
        '[models."kimi-code/k3"]\n'
        'model = "k3"\n'
    )
    assert config_has_usable_default_model(managed) is False
    alias_only = f'default_model = "{DEFAULT_KIMI_CODE_MODEL}"\n'
    assert config_has_usable_default_model(alias_only) is False


def test_cli_preflight_blockers_include_no_model():
    assert NAMED_BLOCKER_CLI_NO_MODEL in CLI_PREFLIGHT_BLOCKERS
    assert issubclass(CodeCliNoModelConfigured, CodeCliUnavailable)


def test_classify_cli_exit_names_no_model_configured():
    """Decided by the Factory's own model configuration, not the CLI's words."""
    blocker, detail = classify_cli_exit(1, "segfault", model_configured=False)
    assert blocker == NAMED_BLOCKER_CLI_NO_MODEL
    assert CodeCliNoModelConfigured.blocker == NAMED_BLOCKER_CLI_NO_MODEL
    assert NAMED_BLOCKER_CLI_NO_MODEL in detail
    assert "No model configured" in detail
    assert "KIMI_CODE_MODEL" in detail
    assert DEFAULT_KIMI_CODE_MODEL in detail
    assert "templated" in detail
    generic, generic_detail = classify_cli_exit(1, "segfault")
    assert generic == NAMED_BLOCKER_CLI_FAILED
    assert generic_detail == "CLI exited 1"
    # The phrase alone, with a usable configuration, decides nothing.
    worded, _ = classify_cli_exit(
        1, "error: failed to run prompt: No model configured.", model_configured=True
    )
    assert worded == NAMED_BLOCKER_CLI_FAILED


def test_classify_cli_exit_names_billing_by_provider_status():
    """The ACCOUNT refused: the provider answered 401 or 402, in any of the
    shapes a CLI prints a status. The words around it decide nothing."""
    blocker, detail = classify_cli_exit(
        1,
        "Error code: 402 - {'error': {'message': 'zorblat', 'type': 'x'}}",
    )
    assert blocker == NAMED_BLOCKER_CLI_BILLING
    assert CodeCliBillingFailed.blocker == NAMED_BLOCKER_CLI_BILLING
    assert issubclass(CodeCliBillingFailed, CodeCliFailed)
    assert NAMED_BLOCKER_CLI_BILLING in CLI_AUTH_BILLING_BLOCKERS
    assert NAMED_BLOCKER_CLI_BILLING in detail
    assert "FACTORY_CODE_CLI_FAILED" in detail
    assert classify_cli_exit(1, "HTTP/1.1 401 from the provider")[0] == NAMED_BLOCKER_CLI_BILLING
    assert classify_cli_exit(1, '{"error": {"code": 402}}')[0] == NAMED_BLOCKER_CLI_BILLING
    # The recorded Moonshot answer (sess_d5789a91) is a 429: account-side.
    photographed, _ = classify_cli_exit(
        1,
        "429 Too Many Requests — this account has been suspended due to "
        "insufficient balance",
    )
    assert photographed == NAMED_BLOCKER_CLI_BILLING
    # Words with no status: not a billing answer.
    words, _ = classify_cli_exit(1, "error: insufficient balance, account suspended")
    assert words == NAMED_BLOCKER_CLI_FAILED
    # A server-side 5xx is not the account.
    generic, _ = classify_cli_exit(1, "HTTP 503 from the provider")
    assert generic == NAMED_BLOCKER_CLI_FAILED


def test_classify_cli_exit_names_model_denied():
    blocker, detail = classify_cli_exit(
        1,
        "error: failed to run prompt: 404 Not Found — for model kimi-code/k3 (k3)",
    )
    assert blocker == NAMED_BLOCKER_CLI_MODEL_DENIED
    assert CodeCliModelDenied.blocker == NAMED_BLOCKER_CLI_MODEL_DENIED
    assert "kimi-k3" in detail
    assert "KIMI_CODE_MODEL" in detail
    assert "templated" in detail
    perm, perm_detail = classify_cli_exit(1, "HTTP 403 for model zorblat-9")
    assert perm == NAMED_BLOCKER_CLI_MODEL_DENIED
    assert "Moonshot" in perm_detail
    # A 3-digit number that is not presented as a status is not one.
    assert classify_cli_exit(1, "wrote 404 lines")[0] == NAMED_BLOCKER_CLI_FAILED


def test_provider_statuses_reads_shapes_not_words():
    from app.factory.build.coder_session import provider_statuses

    assert provider_statuses("HTTP 429") == [429]
    assert provider_statuses("Error code: 402 - {'error': {}}") == [402]
    assert provider_statuses("got 404 Not Found") == [404]
    assert provider_statuses('{"error": {"status": 401}}') == [401]
    assert provider_statuses("processed 404 rows; 500 left") == []


def test_cli_session_honours_owner_stop(tmp_path, monkeypatch):
    script = tmp_path / "fake_coder.sh"
    script.write_text(
        "#!/bin/sh\n"
        "echo starting\n"
        "sleep 30\n"
        "echo never\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    monkeypatch.setenv("FACTORY_CODE_CLI", str(script))
    ctx = _ctx(tmp_path)
    compiled = compile_brief(ctx.blueprint, ctx.plan, store_ids={"analytics"})
    ctx.workspace.write_text(Path("docs") / "coder_brief.md", compiled.text)
    ctx.workspace.write_text(Path("docs") / "coder_session.log", "")
    write_control(tmp_path / "build", "stop")
    result = dispatch_compiled_brief(ctx, compiled)
    assert result.ok is False
    assert result.blocker == NAMED_BLOCKER_STOPPED
    log = read_log_tail(tmp_path / "build")
    assert "STOP" in log or result.detail


def test_specs_from_models_source_reads_fields_and_constraints():
    source = (
        "class Appointment:\n"
        "    FIELDS = ['reference', 'status', 'pet_name']\n"
        "    CONSTRAINTS = {'status': {'allowed_values': ['open', 'closed']}}\n"
        "    ENTITY = 'appointment'\n"
        "\n"
        "MODELS = {'appointment_scheduling': Appointment}\n"
    )
    specs = specs_from_models_source(source)
    assert "appointment_scheduling" in specs
    names = [f["name"] for f in specs["appointment_scheduling"]["fields"]]
    assert names == ["reference", "status", "pet_name"]
    status = next(
        f for f in specs["appointment_scheduling"]["fields"] if f["name"] == "status"
    )
    assert status["allowed_values"] == ["open", "closed"]


_PREPARED_EVENT_BUS_HANDLER = (
    "def handle(payload):\n"
    "    steps = [{\n"
    "        'block': 'event_bus',\n"
    "        'action': 'publish',\n"
    "        'input': {\n"
    "            'topic': 'reminder.due',\n"
    "            'payload': {'reference': payload.get('reference')},\n"
    "                        'message': 'reminder recorded',\n"
            "            'channel': 'mcp',\n"
            "            'tool': 'event_bus',\n"
            "        },\n"
    "    }]\n"
    "    return execute('workflow', {'steps': steps}, action='run')\n"
)

_UNPREPARED_EVENT_BUS_HANDLER = (
    "def handle(payload):\n"
    "    steps = [{'block': 'event_bus', 'input': payload}]\n"
    "    return execute('workflow', {'steps': steps})\n"
)


def test_harvest_does_not_keep_publish_keys_without_mcp_tool(tmp_path):
    """Live sess_d5789a91: topic/payload/message/mcp without tool → step_0."""
    missing_tool = _PREPARED_EVENT_BUS_HANDLER.replace(
        "            'tool': 'event_bus',\n",
        "",
    )
    assert "tool" not in missing_tool
    root = tmp_path / "ws"
    actions = root / "app" / "actions"
    actions.mkdir(parents=True)
    (actions / "automated_reminders.py").write_text(missing_tool, encoding="utf-8")
    assert _is_keepable_handler(missing_tool) is False
    specs, kept = harvest_cli_artifacts(root, ["automated_reminders"])
    assert kept == []
    assert specs == {}


def test_harvest_keeps_brief_driven_workflow_steps_without_capability_id(tmp_path):
    """#317 keepable required CAPABILITY_ID; CLI workflow modules often omit it.

    After #318 the keep-path treated unprepared ``input: payload`` as
    brief-driven. Only the prepared PRODUCT contract is keepable.
    """
    root = tmp_path / "ws"
    actions = root / "app" / "actions"
    actions.mkdir(parents=True)
    (actions / "reminders_and_notifications.py").write_text(
        _PREPARED_EVENT_BUS_HANDLER, encoding="utf-8"
    )
    (actions / "clinic_intake.py").write_text(
        "# fragment — not a keepable handler\nreturn {}\n",
        encoding="utf-8",
    )
    assert _has_brief_workflow_steps(
        (actions / "reminders_and_notifications.py").read_text(encoding="utf-8")
    )
    assert _is_keepable_handler(
        (actions / "reminders_and_notifications.py").read_text(encoding="utf-8")
    )
    specs, kept = harvest_cli_artifacts(
        root, ["reminders_and_notifications", "clinic_intake"]
    )
    assert kept == ["reminders_and_notifications"]
    assert specs == {}


def test_harvest_does_not_keep_unprepared_event_bus_forward(tmp_path):
    """Live sess_a4690fb: CLI wrote {'block': 'event_bus', 'input': payload}."""
    root = tmp_path / "ws"
    actions = root / "app" / "actions"
    actions.mkdir(parents=True)
    (actions / "appointment_scheduling.py").write_text(
        "CAPABILITY_ID = 'appointment_scheduling'\n" + _UNPREPARED_EVENT_BUS_HANDLER,
        encoding="utf-8",
    )
    (actions / "reminders_notifications.py").write_text(
        _UNPREPARED_EVENT_BUS_HANDLER, encoding="utf-8"
    )
    assert _is_keepable_handler(
        (actions / "appointment_scheduling.py").read_text(encoding="utf-8")
    ) is False
    specs, kept = harvest_cli_artifacts(
        root, ["appointment_scheduling", "reminders_notifications"]
    )
    assert kept == []
    assert specs == {}
    assert harvest_unkeepable_event_bus_ids(
        root, ["appointment_scheduling", "reminders_notifications"]
    ) == ["appointment_scheduling", "reminders_notifications"]


_FACTORY_WRAP_UNPREPARED = (
    "def _watched(block_id, *a, **kw):\n"
    "    prepared = _prepare_block_input(block_id, data, action=action)\n"
    "    return _dispatch.execute(block_id, prepared, action=action)\n"
    "def handle(payload):\n"
    "    steps = [{'block': 'event_bus', 'input': payload}]\n"
    "    return execute('workflow', {'steps': steps})\n"
)


def test_harvest_does_not_keep_factory_wrap_around_unprepared_step1(tmp_path):
    """Wrap tokens must not make appointment_scheduling step_1 keepable."""
    root = tmp_path / "ws"
    actions = root / "app" / "actions"
    actions.mkdir(parents=True)
    (actions / "appointment_scheduling.py").write_text(
        "CAPABILITY_ID = 'appointment_scheduling'\n" + _FACTORY_WRAP_UNPREPARED,
        encoding="utf-8",
    )
    assert _is_keepable_handler(
        (actions / "appointment_scheduling.py").read_text(encoding="utf-8")
    ) is False
    specs, kept = harvest_cli_artifacts(root, ["appointment_scheduling"])
    assert kept == []
    assert specs == {}


_MIXED_STEP2_EVENT_BUS_HANDLER = (
    "def handle(payload):\n"
    "    steps = [\n"
    "        {\n"
    "            'block': 'event_bus',\n"
    "            'action': 'publish',\n"
    "            'input': {\n"
    "                'topic': 'appointment.booked',\n"
    "                'payload': {'reference': payload.get('reference')},\n"
    "                'message': 'booked',\n"
    "                'channel': 'mcp',\n"
    "            },\n"
    "        },\n"
    "        {'block': 'event_bus', 'input': payload},\n"
    "    ]\n"
    "    return execute('workflow', {'steps': steps}, action='run')\n"
)


def test_harvest_does_not_keep_prepared_step1_raw_step2(tmp_path):
    """Live sess_d70c18ef: appointment_booking step_2 still forwarded payload."""
    root = tmp_path / "ws"
    actions = root / "app" / "actions"
    actions.mkdir(parents=True)
    (actions / "appointment_booking.py").write_text(
        "CAPABILITY_ID = 'appointment_booking'\n" + _MIXED_STEP2_EVENT_BUS_HANDLER,
        encoding="utf-8",
    )
    assert _is_keepable_handler(
        (actions / "appointment_booking.py").read_text(encoding="utf-8")
    ) is False
    specs, kept = harvest_cli_artifacts(root, ["appointment_booking"])
    assert kept == []
    assert specs == {}


def test_harvest_cli_body_does_not_overwrite_workspace_workflow_steps(tmp_path):
    """Same-session CLI: thin JSON body must not replace brief-driven steps."""
    root = tmp_path / "ws"
    actions = root / "app" / "actions"
    actions.mkdir(parents=True)
    (actions / "reminders_and_notifications.py").write_text(
        _PREPARED_EVENT_BUS_HANDLER, encoding="utf-8"
    )
    result = DispatchResult(
        via="cli",
        ok=True,
        detail="cli",
        handlers={
            "reminders_and_notifications": (
                "return {'ok': True, 'capability': 'reminders_and_notifications'}"
            )
        },
    )
    _merge_workspace_harvest(
        result, root, ["reminders_and_notifications"]
    )
    assert "reminders_and_notifications" in result.kept_handler_ids
    assert "reminders_and_notifications" not in result.handlers


def test_harvest_cli_does_not_prefer_unprepared_disk_over_json_body(tmp_path):
    """Unprepared disk must not lock in; factory wrap should get the body."""
    root = tmp_path / "ws"
    actions = root / "app" / "actions"
    actions.mkdir(parents=True)
    (actions / "appointment_scheduling.py").write_text(
        _UNPREPARED_EVENT_BUS_HANDLER, encoding="utf-8"
    )
    body = "return {'ok': True, 'capability': 'appointment_scheduling'}"
    result = DispatchResult(
        via="cli",
        ok=True,
        detail="cli",
        handlers={"appointment_scheduling": body},
    )
    _merge_workspace_harvest(result, root, ["appointment_scheduling"])
    assert result.handlers["appointment_scheduling"] == body
    assert "appointment_scheduling" not in result.kept_handler_ids


def test_harvest_cli_does_not_prefer_mixed_step2_disk(tmp_path):
    """Prepared step_1 + raw step_2 must not pin appointment_booking."""
    root = tmp_path / "ws"
    actions = root / "app" / "actions"
    actions.mkdir(parents=True)
    (actions / "appointment_booking.py").write_text(
        _MIXED_STEP2_EVENT_BUS_HANDLER, encoding="utf-8"
    )
    body = "return {'ok': True, 'capability': 'appointment_booking'}"
    result = DispatchResult(
        via="cli",
        ok=True,
        detail="cli",
        handlers={"appointment_booking": body},
    )
    _merge_workspace_harvest(result, root, ["appointment_booking"])
    assert result.handlers["appointment_booking"] == body
    assert "appointment_booking" not in result.kept_handler_ids


def test_harvest_oneshot_does_not_pin_a_previous_round_handler(tmp_path):
    """Rework oneshot must keep its body so a red PRODUCT handler can change."""
    root = tmp_path / "ws"
    actions = root / "app" / "actions"
    actions.mkdir(parents=True)
    (actions / "reminders_and_notifications.py").write_text(
        _UNPREPARED_EVENT_BUS_HANDLER, encoding="utf-8"
    )
    body = "return {'ok': True, 'capability': 'reminders_and_notifications'}"
    result = DispatchResult(
        via="http_oneshot",
        ok=True,
        detail="oneshot",
        handlers={"reminders_and_notifications": body},
    )
    _merge_workspace_harvest(
        result, root, ["reminders_and_notifications"]
    )
    assert result.handlers["reminders_and_notifications"] == body
    assert "reminders_and_notifications" not in result.kept_handler_ids


def test_harvest_cli_artifacts_keeps_handle_modules(tmp_path):
    root = tmp_path / "ws"
    actions = root / "app" / "actions"
    actions.mkdir(parents=True)
    (root / "app" / "models.py").write_text(
        "class Reminder:\n"
        "    FIELDS = ['reference', 'status']\n"
        "    CONSTRAINTS = {}\n"
        "MODELS = {'automated_reminders': Reminder}\n",
        encoding="utf-8",
    )
    (actions / "automated_reminders.py").write_text(
        'CAPABILITY_ID = "automated_reminders"\n'
        "def handle(payload):\n"
        "    return {'ok': True, 'capability': CAPABILITY_ID}\n",
        encoding="utf-8",
    )
    (actions / "clinic_intake.py").write_text(
        "# fragment — not a keepable handler\nreturn {}\n",
        encoding="utf-8",
    )
    specs, kept = harvest_cli_artifacts(
        root, ["automated_reminders", "clinic_intake"]
    )
    assert "automated_reminders" in specs
    assert kept == ["automated_reminders"]


def test_cli_dispatch_harvests_workspace_specs_and_handlers(tmp_path, monkeypatch):
    script = tmp_path / "fake_coder.sh"
    dest = tmp_path / "build"
    script.write_text(
        "#!/bin/sh\n"
        "mkdir -p app/actions\n"
        "cat > app/models.py << 'EOF'\n"
        "class Appointment:\n"
        "    FIELDS = ['reference', 'status', 'pet_name']\n"
        "    CONSTRAINTS = {'status': {'allowed_values': ['open', 'closed']}}\n"
        "MODELS = {'analytics_surface': Appointment}\n"
        "EOF\n"
        "cat > app/actions/analytics_surface.py << 'EOF'\n"
        'CAPABILITY_ID = "analytics_surface"\n'
        "def handle(payload):\n"
        "    return {'ok': True, 'capability': CAPABILITY_ID}\n"
        "EOF\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    monkeypatch.setenv("FACTORY_CODE_CLI", str(script))
    ctx = _ctx(tmp_path)
    compiled = compile_brief(ctx.blueprint, ctx.plan, store_ids={"analytics"})
    ctx.workspace.write_text(Path("docs") / "coder_brief.md", compiled.text)
    ctx.workspace.write_text(Path("docs") / "coder_session.log", "")
    result = dispatch_compiled_brief(ctx, compiled)
    assert result.ok, result.detail
    assert result.via == "cli"
    assert "analytics_surface" in result.specs
    names = [f["name"] for f in result.specs["analytics_surface"]["fields"]]
    assert "pet_name" in names
    assert result.kept_handler_ids == ["analytics_surface"]
    receipt = json.loads(
        (dest / "docs" / "coder_receipt.json").read_text(encoding="utf-8")
    )
    assert receipt["kept_handler_ids"] == ["analytics_surface"]


def test_start_runner_build_refuses_when_cli_missing(tmp_path, monkeypatch):
    from app.factory.build_jobs import start_runner_build
    from app.factory.blueprint import load_blueprint

    monkeypatch.setenv("FACTORY_CODER_ENABLED", "1")
    monkeypatch.setenv("FACTORY_BRIEF_REQUIRE_CLI", "1")
    monkeypatch.delenv("FACTORY_BRIEF_HTTP_ONESHOT", raising=False)
    monkeypatch.setenv("FACTORY_CODE_CLI", "/no/such/coder")
    monkeypatch.setattr(
        "app.factory.build.coder_session.cli_available",
        lambda command=None: False,
    )
    root = Path(__file__).resolve().parents[3]
    bp = load_blueprint(root / "blueprints/examples/runner_smoke.yaml")
    with pytest.raises(CodeCliUnavailable, match=NAMED_BLOCKER_CLI):
        start_runner_build(bp, tmp_path / "out")
    assert not (tmp_path / "out" / "build_ledger.jsonl").exists()


def test_approve_and_generate_names_cli_class_without_takeover(tmp_path, monkeypatch):
    from app.factory import platform_chat_flow
    from app.models.session import ProductDesignState, SessionState

    monkeypatch.setenv("FACTORY_CODER_ENABLED", "1")
    monkeypatch.setenv("FACTORY_BRIEF_REQUIRE_CLI", "1")
    monkeypatch.delenv("FACTORY_BRIEF_HTTP_ONESHOT", raising=False)
    monkeypatch.setenv("FACTORY_CODE_CLI", "/no/such/coder")
    monkeypatch.setattr(
        "app.factory.build.coder_session.cli_available",
        lambda command=None: False,
    )
    root = Path(__file__).resolve().parents[3]
    from app.factory.blueprint import load_blueprint

    bp = load_blueprint(root / "blueprints/examples/runner_smoke.yaml")
    state = SessionState(
        session_id="sess_cli_missing",
        user_id="test-user",
        product_design=ProductDesignState(
            blueprint=bp.model_dump(mode="json"),
            blueprint_approved=False,
        ),
    )
    result = platform_chat_flow.approve_and_generate(state, output_root=tmp_path)
    assert result["ok"] is False
    assert result["sse"] == "error"
    assert result["blocker"] == NAMED_BLOCKER_CLI
    assert NAMED_BLOCKER_CLI in result["summary"]
    assert "never opened" in result["summary"]
    assert state.product_design.generation is None
    assert NAMED_BLOCKER_CLI in (state.product_design.last_error or "")


def test_cli_available_sees_an_executable(tmp_path, monkeypatch):
    script = tmp_path / "kimi"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setenv("FACTORY_CODE_CLI", str(script))
    assert cli_available() is True
    monkeypatch.setenv("FACTORY_CODE_CLI", "/no/such/coder")
    assert cli_available() is False


def _fake_kimi(tmp_path: Path) -> Path:
    script = tmp_path / "kimi"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)
    return script


def _usable_kimi_toml() -> str:
    return (
        f'default_model = "{DEFAULT_KIMI_CODE_MODEL}"\n'
        "\n"
        "[providers.kimi]\n"
        'type = "kimi"\n'
        'api_key = "sk-test-not-real"\n'
        'base_url = "https://api.moonshot.ai/v1"\n'
        "\n"
        f'[models."{DEFAULT_KIMI_CODE_MODEL}"]\n'
        'provider = "kimi"\n'
        f'model = "{DEFAULT_KIMI_CODE_MODEL_ID}"\n'
        "max_context_size = 1048576\n"
    )


def _credentials_only_toml() -> str:
    return (
        "[providers.kimi]\n"
        'type = "kimi"\n'
        'api_key = "sk-test-not-real"\n'
        'base_url = "https://api.moonshot.ai/v1"\n'
    )


def _require_cli(monkeypatch) -> None:
    from app.core.llm_config import CURSOR_KEY_ENVS

    monkeypatch.setenv("FACTORY_CODER_ENABLED", "1")
    monkeypatch.setenv("FACTORY_BRIEF_REQUIRE_CLI", "1")
    monkeypatch.delenv("FACTORY_BRIEF_HTTP_ONESHOT", raising=False)
    monkeypatch.delenv("FACTORY_BRIEF_DISPATCH", raising=False)
    for name in CURSOR_KEY_ENVS:
        monkeypatch.delenv(name, raising=False)


def test_dispatch_kimi_without_config_toml_fail_closed(tmp_path, monkeypatch):
    """Binary on PATH + no config.toml must not open a WRITER CLI session."""
    script = _fake_kimi(tmp_path)
    _require_cli(monkeypatch)
    monkeypatch.setenv("FACTORY_CODE_CLI", str(script))
    monkeypatch.setenv("KIMI_CODE_HOME", str(tmp_path / "no-kimi-home"))
    monkeypatch.delenv("KIMI_CODE_API_KEY", raising=False)
    monkeypatch.delenv("KIMI_CODE_KEY", raising=False)
    oneshot = []
    monkeypatch.setattr(
        "app.factory.coder.generate_from_compiled_brief",
        lambda **kw: oneshot.append(kw) or {"specs": {}, "handlers": {}, "model": "x"},
    )
    ran = []
    monkeypatch.setattr(
        "app.factory.build.coder_session._run_cli_session",
        lambda *a, **kw: ran.append(True) or DispatchResult(via="cli", ok=True, detail="nope"),
    )
    ctx = _ctx(tmp_path)
    compiled = compile_brief(ctx.blueprint, ctx.plan, store_ids={"analytics"})
    ctx.workspace.write_text(Path("docs") / "coder_brief.md", compiled.text)
    ctx.workspace.write_text(Path("docs") / "coder_session.log", "")
    result = dispatch_compiled_brief(ctx, compiled)
    assert result.ok is False
    assert result.via == "unavailable"
    assert result.blocker == NAMED_BLOCKER_CLI_CREDS
    assert NAMED_BLOCKER_CLI_CREDS in result.detail
    assert "credentials_file_present=false" in result.detail
    assert oneshot == []
    assert ran == [], "missing credentials must not start FACTORY_CODE_CLI"
    receipt = json.loads(
        (tmp_path / "build" / "docs" / "coder_receipt.json").read_text(encoding="utf-8")
    )
    assert receipt["blocker"] == NAMED_BLOCKER_CLI_CREDS
    log = read_log_tail(tmp_path / "build")
    assert NAMED_BLOCKER_CLI_CREDS in log
    assert "falling back to HTTP oneshot" not in log


def test_dispatch_cli_message_alone_does_not_name_no_model(tmp_path, monkeypatch):
    """The model configuration is usable; the CLI printing 'No model configured'
    does not decide the class -- a non-zero exit is FACTORY_CODE_CLI_FAILED and
    still never falls back to the HTTP oneshot."""
    script = tmp_path / "kimi"
    script.write_text(
        "#!/bin/sh\n"
        "echo \"error: failed to run prompt: No model configured. "
        "Run 'kimi' and use /login to sign in, then retry; "
        "or set default model in config.toml.\" >&2\n"
        "exit 1\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    home = tmp_path / "kimi-home"
    home.mkdir()
    (home / "config.toml").write_text(_usable_kimi_toml(), encoding="utf-8")
    _require_cli(monkeypatch)
    monkeypatch.setenv("FACTORY_CODE_CLI", str(script))
    monkeypatch.setenv("KIMI_CODE_HOME", str(home))
    oneshot = []
    monkeypatch.setattr(
        "app.factory.coder.generate_from_compiled_brief",
        lambda **kw: oneshot.append(kw) or {"specs": {}, "handlers": {}, "model": "x"},
    )
    ctx = _ctx(tmp_path)
    compiled = compile_brief(ctx.blueprint, ctx.plan, store_ids={"analytics"})
    ctx.workspace.write_text(Path("docs") / "coder_brief.md", compiled.text)
    ctx.workspace.write_text(Path("docs") / "coder_session.log", "")
    result = dispatch_compiled_brief(ctx, compiled)
    assert result.ok is False
    assert result.via == "cli"
    assert result.blocker == NAMED_BLOCKER_CLI_FAILED
    assert oneshot == [], "CLI no-model must not fall back to HTTP oneshot"
    receipt = json.loads(
        (tmp_path / "build" / "docs" / "coder_receipt.json").read_text(encoding="utf-8")
    )
    assert receipt["blocker"] == NAMED_BLOCKER_CLI_FAILED
    failures = ctx.state.get("coder_failures") or {}
    assert NAMED_BLOCKER_CLI_NO_MODEL not in failures.get("brief_dispatch", "")


def test_dispatch_cli_model_denied_is_named_class(tmp_path, monkeypatch):
    """CLI 404 / Permission denied on the model is a named fail-closed class."""
    script = tmp_path / "kimi"
    script.write_text(
        "#!/bin/sh\n"
        "echo \"error: failed to run prompt: 404 Not Found / "
        "Permission denied for model kimi-code/k3\" >&2\n"
        "exit 1\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    home = tmp_path / "kimi-home"
    home.mkdir()
    (home / "config.toml").write_text(_usable_kimi_toml(), encoding="utf-8")
    _require_cli(monkeypatch)
    monkeypatch.setenv("FACTORY_CODE_CLI", str(script))
    monkeypatch.setenv("KIMI_CODE_HOME", str(home))
    oneshot = []
    monkeypatch.setattr(
        "app.factory.coder.generate_from_compiled_brief",
        lambda **kw: oneshot.append(kw) or {"specs": {}, "handlers": {}, "model": "x"},
    )
    ctx = _ctx(tmp_path)
    compiled = compile_brief(ctx.blueprint, ctx.plan, store_ids={"analytics"})
    ctx.workspace.write_text(Path("docs") / "coder_brief.md", compiled.text)
    ctx.workspace.write_text(Path("docs") / "coder_session.log", "")
    result = dispatch_compiled_brief(ctx, compiled)
    assert result.ok is False
    assert result.via == "cli"
    assert result.blocker == NAMED_BLOCKER_CLI_MODEL_DENIED
    assert "Permission denied" in result.detail
    assert oneshot == [], "CLI model-denied must not fall back to HTTP oneshot"
    receipt = json.loads(
        (tmp_path / "build" / "docs" / "coder_receipt.json").read_text(encoding="utf-8")
    )
    assert receipt["blocker"] == NAMED_BLOCKER_CLI_MODEL_DENIED


def test_dispatch_cli_other_nonzero_stays_failed(tmp_path, monkeypatch):
    script = tmp_path / "kimi"
    script.write_text("#!/bin/sh\necho boom\nexit 1\n", encoding="utf-8")
    script.chmod(0o755)
    home = tmp_path / "kimi-home"
    home.mkdir()
    (home / "config.toml").write_text(_usable_kimi_toml(), encoding="utf-8")
    _require_cli(monkeypatch)
    monkeypatch.setenv("FACTORY_CODE_CLI", str(script))
    monkeypatch.setenv("KIMI_CODE_HOME", str(home))
    ctx = _ctx(tmp_path)
    compiled = compile_brief(ctx.blueprint, ctx.plan, store_ids={"analytics"})
    ctx.workspace.write_text(Path("docs") / "coder_brief.md", compiled.text)
    ctx.workspace.write_text(Path("docs") / "coder_session.log", "")
    result = dispatch_compiled_brief(ctx, compiled)
    assert result.ok is False
    assert result.blocker == NAMED_BLOCKER_CLI_FAILED
    assert result.detail == "CLI exited 1"
    assert result.reuse_keep_path is False
    assert result.kept_handler_ids == []


def test_dispatch_kimi_with_config_toml_credentials_ok(tmp_path, monkeypatch):
    script = _fake_kimi(tmp_path)
    _require_cli(monkeypatch)
    monkeypatch.setenv("FACTORY_CODE_CLI", str(script))
    home = tmp_path / "kimi-home"
    home.mkdir()
    (home / "config.toml").write_text(_usable_kimi_toml(), encoding="utf-8")
    monkeypatch.setenv("KIMI_CODE_HOME", str(home))
    oneshot = []
    monkeypatch.setattr(
        "app.factory.coder.generate_from_compiled_brief",
        lambda **kw: oneshot.append(kw) or {"specs": {}, "handlers": {}, "model": "x"},
    )
    ctx = _ctx(tmp_path)
    compiled = compile_brief(ctx.blueprint, ctx.plan, store_ids={"analytics"})
    ctx.workspace.write_text(Path("docs") / "coder_brief.md", compiled.text)
    ctx.workspace.write_text(Path("docs") / "coder_session.log", "")
    result = dispatch_compiled_brief(ctx, compiled)
    assert result.ok, result.detail
    assert result.via == "cli"
    assert result.blocker is None
    assert oneshot == []
    assert cli_credentials_ok() is True
    assert cli_default_model_ok() is True
    log = read_log_tail(tmp_path / "build")
    assert "--model" in log
    assert DEFAULT_KIMI_CODE_MODEL in log


def test_probe_code_cli_reports_credentials_missing(tmp_path, monkeypatch):
    script = _fake_kimi(tmp_path)
    _require_cli(monkeypatch)
    monkeypatch.setenv("FACTORY_CODE_CLI", str(script))
    monkeypatch.setenv("KIMI_CODE_HOME", str(tmp_path / "empty-kimi-home"))
    probe = probe_code_cli()
    assert probe["available"] is True
    assert probe["credentials_file_present"] is False
    assert probe["requires_cli"] is True
    assert probe["requires_kimi_credentials"] is True
    assert probe["cursor_ba_available"] is False
    assert probe["blocker"] == NAMED_BLOCKER_CLI_CREDS
    assert NAMED_BLOCKER_CLI_CREDS in probe["error"]


def test_probe_code_cli_credentials_ok_when_config_present(tmp_path, monkeypatch):
    script = _fake_kimi(tmp_path)
    _require_cli(monkeypatch)
    monkeypatch.setenv("FACTORY_CODE_CLI", str(script))
    home = tmp_path / "kimi-home"
    home.mkdir()
    (home / "config.toml").write_text(_usable_kimi_toml(), encoding="utf-8")
    monkeypatch.setenv("KIMI_CODE_HOME", str(home))
    probe = probe_code_cli()
    assert probe["available"] is True
    assert probe["credentials_file_present"] is True
    assert probe["default_model_configured"] is True
    assert probe["default_model"] == DEFAULT_KIMI_CODE_MODEL
    assert "blocker" not in probe


def test_claude_cli_does_not_require_kimi_config(tmp_path, monkeypatch):
    script = tmp_path / "claude"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)
    _require_cli(monkeypatch)
    monkeypatch.setenv("FACTORY_CODE_CLI", str(script))
    monkeypatch.setenv("KIMI_CODE_HOME", str(tmp_path / "no-kimi-home"))
    assert cli_credentials_ok() is True
    raise_if_cli_session_unready()
    probe = probe_code_cli()
    assert probe["available"] is True
    assert probe["requires_kimi_credentials"] is False
    assert "blocker" not in probe


def test_env_test_template_path_skips_credentials_gate(tmp_path, monkeypatch):
    """ENV=test without REQUIRE_CLI mutation must not refuse generate-start."""
    script = _fake_kimi(tmp_path)
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "1")
    monkeypatch.delenv("FACTORY_BRIEF_REQUIRE_CLI", raising=False)
    monkeypatch.delenv("FACTORY_BRIEF_HTTP_ONESHOT", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("FACTORY_CODE_PROVIDER", raising=False)
    monkeypatch.setenv("FACTORY_CODE_CLI", str(script))
    monkeypatch.setenv("KIMI_CODE_HOME", str(tmp_path / "no-kimi-home"))
    assert brief_requires_cli() is False
    raise_if_cli_session_unready()
    probe = probe_code_cli()
    assert probe["available"] is True
    assert probe["credentials_file_present"] is False
    assert probe["requires_cli"] is False
    assert "blocker" not in probe


def test_start_runner_build_refuses_when_credentials_missing(tmp_path, monkeypatch):
    from app.factory.build_jobs import start_runner_build
    from app.factory.blueprint import load_blueprint

    script = _fake_kimi(tmp_path)
    _require_cli(monkeypatch)
    monkeypatch.setenv("FACTORY_CODE_CLI", str(script))
    monkeypatch.setenv("KIMI_CODE_HOME", str(tmp_path / "no-kimi-home"))
    root = Path(__file__).resolve().parents[3]
    bp = load_blueprint(root / "blueprints/examples/runner_smoke.yaml")
    with pytest.raises(CodeCliCredentialsMissing, match=NAMED_BLOCKER_CLI_CREDS):
        start_runner_build(bp, tmp_path / "out")
    assert not (tmp_path / "out" / "build_ledger.jsonl").exists()


def test_approve_and_generate_names_credentials_class_without_takeover(
    tmp_path, monkeypatch
):
    from app.factory import platform_chat_flow
    from app.models.session import ProductDesignState, SessionState

    script = _fake_kimi(tmp_path)
    _require_cli(monkeypatch)
    monkeypatch.setenv("FACTORY_CODE_CLI", str(script))
    monkeypatch.setenv("KIMI_CODE_HOME", str(tmp_path / "no-kimi-home"))
    root = Path(__file__).resolve().parents[3]
    from app.factory.blueprint import load_blueprint

    bp = load_blueprint(root / "blueprints/examples/runner_smoke.yaml")
    state = SessionState(
        session_id="sess_cli_creds_missing",
        user_id="test-user",
        product_design=ProductDesignState(
            blueprint=bp.model_dump(mode="json"),
            blueprint_approved=False,
        ),
    )
    result = platform_chat_flow.approve_and_generate(state, output_root=tmp_path)
    assert result["ok"] is False
    assert result["sse"] == "error"
    assert result["blocker"] == NAMED_BLOCKER_CLI_CREDS
    assert NAMED_BLOCKER_CLI_CREDS in result["summary"]
    assert "never opened" in result["summary"]
    assert "taken over" not in result["summary"].lower()
    assert state.product_design.generation is None
    assert NAMED_BLOCKER_CLI_CREDS in (state.product_design.last_error or "")


def test_dispatch_credentials_only_toml_is_no_model_preflight(tmp_path, monkeypatch):
    """Live shape: file present, no default_model — never open WRITER CLI."""
    script = _fake_kimi(tmp_path)
    _require_cli(monkeypatch)
    monkeypatch.setenv("FACTORY_CODE_CLI", str(script))
    home = tmp_path / "kimi-home"
    home.mkdir()
    (home / "config.toml").write_text(_credentials_only_toml(), encoding="utf-8")
    monkeypatch.setenv("KIMI_CODE_HOME", str(home))
    oneshot = []
    monkeypatch.setattr(
        "app.factory.coder.generate_from_compiled_brief",
        lambda **kw: oneshot.append(kw) or {"specs": {}, "handlers": {}, "model": "x"},
    )
    ran = []
    monkeypatch.setattr(
        "app.factory.build.coder_session._run_cli_session",
        lambda *a, **kw: ran.append(True)
        or DispatchResult(via="cli", ok=True, detail="nope"),
    )
    ctx = _ctx(tmp_path)
    compiled = compile_brief(ctx.blueprint, ctx.plan, store_ids={"analytics"})
    ctx.workspace.write_text(Path("docs") / "coder_brief.md", compiled.text)
    ctx.workspace.write_text(Path("docs") / "coder_session.log", "")
    result = dispatch_compiled_brief(ctx, compiled)
    assert result.ok is False
    assert result.via == "unavailable"
    assert result.blocker == NAMED_BLOCKER_CLI_NO_MODEL
    assert "credentials_file_present=true" in result.detail
    assert "default_model" in result.detail
    assert oneshot == []
    assert ran == [], "NO_MODEL preflight must not start FACTORY_CODE_CLI"
    receipt = json.loads(
        (tmp_path / "build" / "docs" / "coder_receipt.json").read_text(encoding="utf-8")
    )
    assert receipt["blocker"] == NAMED_BLOCKER_CLI_NO_MODEL
    log = read_log_tail(tmp_path / "build")
    assert NAMED_BLOCKER_CLI_NO_MODEL in log
    assert "falling back to HTTP oneshot" not in log


def test_probe_code_cli_reports_no_model_when_default_missing(tmp_path, monkeypatch):
    script = _fake_kimi(tmp_path)
    _require_cli(monkeypatch)
    monkeypatch.setenv("FACTORY_CODE_CLI", str(script))
    home = tmp_path / "kimi-home"
    home.mkdir()
    (home / "config.toml").write_text(_credentials_only_toml(), encoding="utf-8")
    monkeypatch.setenv("KIMI_CODE_HOME", str(home))
    probe = probe_code_cli()
    assert probe["available"] is True
    assert probe["credentials_file_present"] is True
    assert probe["default_model_configured"] is False
    assert probe["blocker"] == NAMED_BLOCKER_CLI_NO_MODEL
    assert NAMED_BLOCKER_CLI_NO_MODEL in probe["error"]


def test_raise_if_cli_session_unready_names_no_model(tmp_path, monkeypatch):
    script = _fake_kimi(tmp_path)
    _require_cli(monkeypatch)
    monkeypatch.setenv("FACTORY_CODE_CLI", str(script))
    home = tmp_path / "kimi-home"
    home.mkdir()
    (home / "config.toml").write_text(_credentials_only_toml(), encoding="utf-8")
    monkeypatch.setenv("KIMI_CODE_HOME", str(home))
    assert cli_credentials_ok() is True
    assert cli_default_model_ok() is False
    with pytest.raises(CodeCliNoModelConfigured, match=NAMED_BLOCKER_CLI_NO_MODEL):
        raise_if_cli_session_unready()


def test_start_runner_build_refuses_when_default_model_missing(tmp_path, monkeypatch):
    from app.factory.build_jobs import start_runner_build
    from app.factory.blueprint import load_blueprint

    script = _fake_kimi(tmp_path)
    _require_cli(monkeypatch)
    monkeypatch.setenv("FACTORY_CODE_CLI", str(script))
    home = tmp_path / "kimi-home"
    home.mkdir()
    (home / "config.toml").write_text(_credentials_only_toml(), encoding="utf-8")
    monkeypatch.setenv("KIMI_CODE_HOME", str(home))
    root = Path(__file__).resolve().parents[3]
    bp = load_blueprint(root / "blueprints/examples/runner_smoke.yaml")
    with pytest.raises(CodeCliNoModelConfigured, match=NAMED_BLOCKER_CLI_NO_MODEL):
        start_runner_build(bp, tmp_path / "out")
    assert not (tmp_path / "out" / "build_ledger.jsonl").exists()


def test_approve_and_generate_names_no_model_class_without_takeover(
    tmp_path, monkeypatch
):
    from app.factory import platform_chat_flow
    from app.models.session import ProductDesignState, SessionState

    script = _fake_kimi(tmp_path)
    _require_cli(monkeypatch)
    monkeypatch.setenv("FACTORY_CODE_CLI", str(script))
    home = tmp_path / "kimi-home"
    home.mkdir()
    (home / "config.toml").write_text(_credentials_only_toml(), encoding="utf-8")
    monkeypatch.setenv("KIMI_CODE_HOME", str(home))
    root = Path(__file__).resolve().parents[3]
    from app.factory.blueprint import load_blueprint

    bp = load_blueprint(root / "blueprints/examples/runner_smoke.yaml")
    state = SessionState(
        session_id="sess_cli_no_model",
        user_id="test-user",
        product_design=ProductDesignState(
            blueprint=bp.model_dump(mode="json"),
            blueprint_approved=False,
        ),
    )
    result = platform_chat_flow.approve_and_generate(state, output_root=tmp_path)
    assert result["ok"] is False
    assert result["sse"] == "error"
    assert result["blocker"] == NAMED_BLOCKER_CLI_NO_MODEL
    assert NAMED_BLOCKER_CLI_NO_MODEL in result["summary"]
    assert "never opened" in result["summary"]
    assert "taken over" not in result["summary"].lower()
    assert state.product_design.generation is None
    assert NAMED_BLOCKER_CLI_NO_MODEL in (state.product_design.last_error or "")


def test_run_writer_raises_on_no_model_when_inventory_gaps(tmp_path, monkeypatch):
    """Non-empty gaps + NO_MODEL stay fail-closed — no fake pilot_ready."""
    from app.factory.build.roles_handlers import run_writer
    from app.factory.build.roles_models import RoleError

    script = _fake_kimi(tmp_path)
    _require_cli(monkeypatch)
    monkeypatch.setenv("FACTORY_CODE_CLI", str(script))
    home = tmp_path / "kimi-home"
    home.mkdir()
    (home / "config.toml").write_text(_usable_kimi_toml(), encoding="utf-8")
    monkeypatch.setenv("KIMI_CODE_HOME", str(home))

    class _GapCap:
        capability_id = "novel_clinic_ai"
        block_ids = ()
        strategy = "GENERATE"
        notes = "gap"

    class _GapPlan:
        capabilities = (_GapCap(),)

    ctx = _ctx(tmp_path)
    ctx.plan = _GapPlan()
    compiled = compile_brief(ctx.blueprint, ctx.plan, store_ids={"analytics"})
    assert any(item.is_gap for item in compiled.inventory)
    monkeypatch.setattr(
        "app.factory.build.brief_compiler.compile_brief_from_ctx",
        lambda _ctx: compiled,
    )
    monkeypatch.setattr(
        "app.factory.build.brief_compiler.verify_inventory",
        lambda _compiled: None,
    )
    monkeypatch.setattr(
        "app.factory.build.brief_lint.lint_or_raise",
        lambda _compiled: None,
    )
    monkeypatch.setattr(
        "app.factory.build.coder_session.dispatch_compiled_brief",
        lambda _ctx, _compiled: DispatchResult(
            via="cli",
            ok=False,
            detail=f"{NAMED_BLOCKER_CLI_NO_MODEL}: CLI exited 1 — No model configured.",
            blocker=NAMED_BLOCKER_CLI_NO_MODEL,
        ),
    )
    with pytest.raises(RoleError, match=NAMED_BLOCKER_CLI_NO_MODEL):
        run_writer(ctx)


def test_empty_gap_cli_billing_fail_harvests_factory_grounded_reuse(
    tmp_path, monkeypatch
):
    """C-BRIEF sess_d5789a91: empty gaps + CLI 429 still harvests REUSE keep-path."""
    script = tmp_path / "kimi"
    script.write_text(
        "#!/bin/sh\n"
        "echo '429 Too Many Requests — this account has been suspended "
        "due to insufficient balance'\n"
        "exit 1\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    home = tmp_path / "kimi-home"
    home.mkdir()
    (home / "config.toml").write_text(_usable_kimi_toml(), encoding="utf-8")
    _require_cli(monkeypatch)
    monkeypatch.setenv("FACTORY_CODE_CLI", str(script))
    monkeypatch.setenv("KIMI_CODE_HOME", str(home))
    oneshot = []
    monkeypatch.setattr(
        "app.factory.coder.generate_from_compiled_brief",
        lambda **kw: oneshot.append(kw) or {"specs": {}, "handlers": {}, "model": "x"},
    )

    class _Sched:
        capability_id = "appointment_scheduling"
        block_ids = ("event_bus", "workflow", "database")
        strategy = "REUSE"
        notes = "schedule"

    class _Dash:
        capability_id = "clinic_dashboard"
        block_ids = ("dashboard",)
        strategy = "REUSE"
        notes = "dash"

    class _Plan:
        capabilities = (_Sched(), _Dash())

    class _Blueprint:
        product_name = "VetCare Hub"
        product_id = "veterinary-care"
        vertical = "veterinary_care"
        summary = "Sample plan"

    ws = RoleWorkspace(BuildRole.WRITER, tmp_path / "build")
    ctx = RoleContext(
        role=BuildRole.WRITER,
        workspace=ws,
        blueprint=_Blueprint(),
        plan=_Plan(),
        state={},
    )
    compiled = compile_brief(
        ctx.blueprint,
        ctx.plan,
        store_ids={"event_bus", "workflow", "database", "dashboard"},
    )
    assert inventory_gap_ids(compiled) == []
    ctx.workspace.write_text(Path("docs") / "coder_brief.md", compiled.text)
    ctx.workspace.write_text(Path("docs") / "coder_session.log", "")
    result = dispatch_compiled_brief(ctx, compiled)
    assert result.ok is False
    assert result.blocker == NAMED_BLOCKER_CLI_BILLING
    assert result.reuse_keep_path is True
    assert oneshot == [], "billing miss must not enable HTTP oneshot"
    assert "appointment_scheduling" in result.kept_handler_ids
    assert "clinic_dashboard" in result.kept_handler_ids
    receipt = json.loads(
        (tmp_path / "build" / "docs" / "coder_receipt.json").read_text(encoding="utf-8")
    )
    assert receipt["ok"] is False
    assert receipt["blocker"] == NAMED_BLOCKER_CLI_BILLING
    assert receipt["honesty_class"] == NAMED_BLOCKER_CLI_FAILED
    assert receipt["keep_path"] == KEEP_PATH_FACTORY_GROUNDED_REUSE
    assert receipt["inventory_gaps"] == []
    assert "appointment_scheduling" in receipt["kept_handler_ids"]
    sched = (
        tmp_path / "build" / "app" / "actions" / "appointment_scheduling.py"
    ).read_text(encoding="utf-8")
    assert "_persist_record(" not in sched
    assert "def handle" in sched
    assert "event_bus" in sched
    assert "factory-grounded" in sched
    dash = (tmp_path / "build" / "app" / "actions" / "clinic_dashboard.py").read_text(
        encoding="utf-8"
    )
    assert "_persist_record(" not in dash
    assert "def handle" in dash
    assert "FACTORY_BRIEF_HTTP_ONESHOT" not in sched


def test_nonempty_gap_cli_billing_fail_does_not_fake_keep_path(
    tmp_path, monkeypatch
):
    """GENERATE gap + billing miss: factory LLM is attempted, no fake keep-path."""
    script = tmp_path / "kimi"
    script.write_text(
        "#!/bin/sh\n"
        "echo '429 Too Many Requests — this account has been suspended "
        "due to insufficient balance'\n"
        "exit 1\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    home = tmp_path / "kimi-home"
    home.mkdir()
    (home / "config.toml").write_text(_usable_kimi_toml(), encoding="utf-8")
    _require_cli(monkeypatch)
    monkeypatch.setenv("FACTORY_CODE_CLI", str(script))
    monkeypatch.setenv("KIMI_CODE_HOME", str(home))
    oneshot = []
    monkeypatch.setattr(
        "app.factory.coder.generate_from_compiled_brief",
        lambda **kw: oneshot.append(kw) or {"specs": {}, "handlers": {}, "model": "x"},
    )

    class _Gap:
        capability_id = "novel_clinic_ai"
        block_ids = ()
        strategy = "GENERATE"
        notes = "needs CLI"

    class _Plan:
        capabilities = (_Gap(),)

    ctx = _ctx(tmp_path)
    ctx.plan = _Plan()
    compiled = compile_brief(ctx.blueprint, ctx.plan, store_ids={"analytics"})
    assert inventory_gap_ids(compiled) == ["novel_clinic_ai"]
    ctx.workspace.write_text(Path("docs") / "coder_brief.md", compiled.text)
    ctx.workspace.write_text(Path("docs") / "coder_session.log", "")
    result = dispatch_compiled_brief(ctx, compiled)
    assert result.ok is False
    assert result.blocker == NAMED_BLOCKER_CLI_BILLING
    assert result.reuse_keep_path is False
    assert result.factory_llm_generate_fallthrough is True
    assert result.factory_llm_generate_ids == ["novel_clinic_ai"]
    assert result.factory_llm_written_ids == []
    assert result.kept_handler_ids == []
    assert oneshot, "GENERATE gaps must call the factory coder LLM"
    assert oneshot[0]["capabilities"] == ["novel_clinic_ai"]
    assert "GENERATE-GAP FALLTHROUGH" in oneshot[0]["brief"]
    receipt = json.loads(
        (tmp_path / "build" / "docs" / "coder_receipt.json").read_text(encoding="utf-8")
    )
    assert receipt["ok"] is False
    assert receipt["blocker"] == NAMED_BLOCKER_CLI_BILLING
    assert receipt["honesty_class"] == NAMED_BLOCKER_CLI_FAILED
    assert receipt["keep_path"] is None
    assert receipt["inventory_gaps"] == []
    assert receipt["factory_llm_written_ids"] == []
    assert receipt["generate_persist_ids"] == ["novel_clinic_ai"]
    assert receipt["kept_handler_ids"] == []
    persist = (
        tmp_path / "build" / "app" / "actions" / "novel_clinic_ai.py"
    ).read_text(encoding="utf-8")
    assert "_persist_record(" not in persist
    assert "def handle" in persist
    assert "factory-grounded persist" in persist
    assert "deterministic contract template" not in persist


def test_should_factory_llm_generate_gaps_only_on_named_cli_miss(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("FACTORY_CODE_PROVIDER", raising=False)
    compiled = compile_brief(
        _Blueprint(),
        type("P", (), {"capabilities": (
            type("G", (), {
                "capability_id": "veterinary_care_core",
                "block_ids": (),
                "strategy": "GENERATE",
                "notes": "core",
            })(),
        )})(),
        store_ids={"dashboard"},
    )
    billing = DispatchResult(
        via="cli",
        ok=False,
        detail="FACTORY_CODE_CLI_BILLING: 429",
        blocker=NAMED_BLOCKER_CLI_BILLING,
    )
    assert should_factory_llm_generate_gaps(compiled, billing) is True
    assert should_keep_factory_grounded_reuse(compiled, billing) is False
    ok = DispatchResult(via="cli", ok=True, detail="done")
    assert should_factory_llm_generate_gaps(compiled, ok) is False
    boom = DispatchResult(
        via="cli", ok=False, detail="CLI exited 1", blocker=NAMED_BLOCKER_CLI_FAILED
    )
    assert should_factory_llm_generate_gaps(compiled, boom) is False
    empty = compile_brief(
        _Blueprint(),
        type("P", (), {"capabilities": (_Cap(),)})(),
        store_ids={"analytics"},
    )
    assert inventory_gap_ids(empty) == []
    assert should_factory_llm_generate_gaps(empty, billing) is False
    assert should_keep_factory_grounded_reuse(empty, billing) is True
    assert should_factory_grounded_generate_persist(compiled, ok) is True
    assert should_factory_grounded_generate_persist(empty, ok) is False
    assert should_factory_grounded_generate_persist(compiled, billing) is False


def test_mutation_coder_session_never_writes_raw_ledger_jsonl():
    """Kills: coder_session opening build_ledger.jsonl instead of ctx.note()."""
    src = (
        Path(__file__).resolve().parents[2]
        / "app"
        / "factory"
        / "build"
        / "coder_session.py"
    ).read_text(encoding="utf-8")
    assert "build_ledger.jsonl" not in src
    assert "ctx.note(" in src
