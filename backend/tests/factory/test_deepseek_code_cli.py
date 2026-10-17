"""DeepSeek V4 Pro under Kimi Code CLI (OpenAI-compat). Not Claude Code.

Do not enable FACTORY_BRIEF_HTTP_ONESHOT. Do not claim pilot_zip.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from app.factory.build.brief_compiler import compile_brief
from app.factory.build.coder_session import (
    NAMED_BLOCKER_CLI,
    NAMED_BLOCKER_CLI_BILLING,
    NAMED_BLOCKER_CLI_CREDS,
    NAMED_BLOCKER_CLI_FAILED,
    NAMED_BLOCKER_CLI_MODEL_DENIED,
    NAMED_BLOCKER_CLI_HUNG_KILLED_BY_WALL,
    NAMED_BLOCKER_CLI_NO_AUTHORSHIP,
    NAMED_BLOCKER_CLI_THIN_AUTHORSHIP,
    NAMED_BLOCKER_CLI_UNUSED,
    brief_dispatch_enabled,
    brief_requires_cli,
    classify_cli_exit,
    cli_credentials_ok,
    cli_requires_deepseek_credentials,
    cli_requires_kimi_credentials,
    deepseek_cli_ready,
    dispatch_compiled_brief,
    ensure_code_cli_credentials,
    inventory_gap_ids,
    probe_code_cli,
    raise_if_cli_session_unready,
    should_factory_llm_generate_gaps,
    thin_stub_success_blocked,
)
from app.factory.build.roles_models import RoleContext, RoleError
from app.factory.build.workspace import RoleWorkspace
from app.factory.build.authority import BuildRole
from app.factory.code_cli import (
    DEFAULT_DEEPSEEK_CLI,
    DEFAULT_DEEPSEEK_FLASH_MODEL,
    DEFAULT_DEEPSEEK_MODEL,
    DEEPSEEK_ANTHROPIC_BASE_URL,
    DEEPSEEK_CODE_MODEL_ENV,
    DEEPSEEK_OPENAI_BASE_URL,
    KIMI_PROMPT_STDIN_INSTRUCTION,
    LEGACY_CLAUDE_OPUS_MODEL,
    REJECTED_DEEPSEEK_CLAUDE_MODEL,
    REJECTED_DEEPSEEK_BARE_MODEL,
    KimiPromptEmpty,
    code_cli_command,
    deepseek_cli_environ,
    deepseek_code_model,
    deepseek_coder_selected,
    factory_code_provider,
    kimi_prompt_argv,
    kimi_prompt_log_argv,
    kimi_prompt_text,
    legacy_deepseek_claude_environ,
    normalize_deepseek_model,
)

# Fake credentials for this test, generated per run (no value is stored in the repo).
_GENERATED_KEY_1 = "sk-" + __import__("secrets").token_hex(11)


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


def _fake_kimi(tmp_path: Path) -> Path:
    script = tmp_path / "kimi"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)
    return script


def _require_cli(monkeypatch) -> None:
    from app.core.llm_config import CURSOR_KEY_ENVS

    monkeypatch.setenv("FACTORY_CODER_ENABLED", "1")
    monkeypatch.setenv("FACTORY_BRIEF_REQUIRE_CLI", "1")
    monkeypatch.delenv("FACTORY_BRIEF_HTTP_ONESHOT", raising=False)
    monkeypatch.delenv("FACTORY_BRIEF_DISPATCH", raising=False)
    for name in CURSOR_KEY_ENVS:
        monkeypatch.delenv(name, raising=False)


def test_deepseek_key_selects_kimi_cli_not_claude(monkeypatch):
    monkeypatch.delenv("FACTORY_CODE_CLI", raising=False)
    monkeypatch.delenv("KIMI_CODE_CLI", raising=False)
    monkeypatch.delenv("FACTORY_CODE_PROVIDER", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", _GENERATED_KEY_1)
    assert factory_code_provider() == "deepseek"
    assert code_cli_command() == "kimi"
    assert code_cli_command() != "claude"
    assert DEFAULT_DEEPSEEK_CLI == "kimi"
    assert deepseek_coder_selected() is True
    assert cli_requires_deepseek_credentials() is True
    assert cli_requires_kimi_credentials() is False


def test_explicit_kimi_cli_plus_deepseek_key_is_deepseek_backend(monkeypatch):
    """FACTORY_CODE_CLI=kimi is the DeepSeek vehicle, not a Moonshot force."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", _GENERATED_KEY_1)
    monkeypatch.setenv("FACTORY_CODE_CLI", "kimi")
    monkeypatch.delenv("FACTORY_CODE_PROVIDER", raising=False)
    assert factory_code_provider() == "deepseek"
    assert code_cli_command() == "kimi"
    assert deepseek_coder_selected() is True
    assert cli_requires_kimi_credentials() is False
    assert cli_requires_deepseek_credentials() is True


def test_explicit_provider_kimi_keeps_moonshot_when_deepseek_key_set(
    monkeypatch, tmp_path
):
    """Historical Moonshot path: FACTORY_CODE_PROVIDER=kimi + KIMI_CODE_API_KEY."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", _GENERATED_KEY_1)
    monkeypatch.setenv("FACTORY_CODE_PROVIDER", "kimi")
    monkeypatch.setenv("FACTORY_CODE_CLI", "kimi")
    monkeypatch.setenv("KIMI_CODE_API_KEY", "sk-kimi-test-not-real")
    monkeypatch.setenv("KIMI_CODE_HOME", str(tmp_path / "kimi-home"))
    monkeypatch.delenv("KIMI_CODE_MODEL", raising=False)
    monkeypatch.delenv("KIMI_CODE_MODEL_ID", raising=False)
    assert factory_code_provider() == "kimi"
    assert code_cli_command() == "kimi"
    assert deepseek_coder_selected() is False
    assert cli_requires_kimi_credentials() is True
    result = ensure_code_cli_credentials()
    assert result["ok"] is True
    text = Path(result["path"]).read_text(encoding="utf-8")
    assert "[providers.kimi]" in text
    assert "sk-kimi-test-not-real" in text
    assert 'default_model = "kimi-k3"' in text
    assert "[providers.deepseek]" not in text


def test_leftover_claude_cli_remaps_to_kimi_when_deepseek(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", _GENERATED_KEY_1)
    monkeypatch.setenv("FACTORY_CODE_CLI", "claude")
    monkeypatch.delenv("FACTORY_CODE_PROVIDER", raising=False)
    assert factory_code_provider() == "deepseek"
    assert code_cli_command() == "kimi"
    assert code_cli_command() != "claude"


def test_provider_deepseek_without_key_requires_credentials(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("FACTORY_CODE_PROVIDER", "deepseek")
    monkeypatch.delenv("FACTORY_CODE_CLI", raising=False)
    assert factory_code_provider() == "deepseek"
    assert code_cli_command() == "kimi"
    assert cli_requires_deepseek_credentials() is True
    assert cli_credentials_ok() is False


def test_deepseek_cli_environ_is_kimi_openai_not_anthropic(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)
    monkeypatch.delenv("DEEPSEEK_CODE_MODEL", raising=False)
    env = deepseek_cli_environ(_GENERATED_KEY_1)
    assert env["DEEPSEEK_API_KEY"] == _GENERATED_KEY_1
    assert env["DEEPSEEK_BASE_URL"] == DEEPSEEK_OPENAI_BASE_URL
    assert env["DEEPSEEK_CODE_MODEL"] == DEFAULT_DEEPSEEK_MODEL
    assert env["KIMI_MODEL_NAME"] == DEFAULT_DEEPSEEK_MODEL
    assert env["KIMI_MODEL_API_KEY"] == _GENERATED_KEY_1
    assert env["KIMI_MODEL_PROVIDER_TYPE"] == "openai"
    assert env["KIMI_MODEL_BASE_URL"] == DEEPSEEK_OPENAI_BASE_URL
    assert env["KIMI_MODEL_MAX_CONTEXT_SIZE"] == "1048576"
    assert "ANTHROPIC_BASE_URL" not in env
    assert "ANTHROPIC_AUTH_TOKEN" not in env
    assert "ANTHROPIC_API_KEY" not in env
    assert "ANTHROPIC_MODEL" not in env
    assert DEFAULT_DEEPSEEK_MODEL.startswith("deepseek-")
    assert DEFAULT_DEEPSEEK_FLASH_MODEL.startswith("deepseek-")
    assert "[" not in DEFAULT_DEEPSEEK_MODEL
    legacy = legacy_deepseek_claude_environ(_GENERATED_KEY_1)
    assert legacy["ANTHROPIC_BASE_URL"] == DEEPSEEK_ANTHROPIC_BASE_URL
    assert set(legacy).isdisjoint(env)


def test_ensure_deepseek_writes_kimi_openai_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("KIMI_CODE_HOME", str(tmp_path / "kimi-home"))
    monkeypatch.delenv("KIMI_CODE_API_KEY", raising=False)
    monkeypatch.delenv("KIMI_CODE_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", _GENERATED_KEY_1)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    result = ensure_code_cli_credentials()
    assert result["ok"] is True
    assert result["deepseek"]["ok"] is True
    assert result["deepseek"]["model"] == DEFAULT_DEEPSEEK_MODEL
    assert result["deepseek"]["cli"] == "kimi"
    dest = tmp_path / "kimi-home" / "config.toml"
    assert dest.is_file()
    text = dest.read_text(encoding="utf-8")
    assert "[providers.deepseek]" in text
    assert 'type = "openai"' in text
    assert "https://api.deepseek.com" in text
    assert _GENERATED_KEY_1 in text
    assert f'default_model = "{DEFAULT_DEEPSEEK_MODEL}"' in text
    assert 'provider = "deepseek"' in text
    assert "ANTHROPIC_BASE_URL" not in __import__("os").environ
    assert "ANTHROPIC_AUTH_TOKEN" not in __import__("os").environ


def test_ensure_kimi_still_writes_when_deepseek_also_set(tmp_path, monkeypatch):
    monkeypatch.setenv("KIMI_CODE_HOME", str(tmp_path / "kimi-home"))
    monkeypatch.setenv("KIMI_CODE_API_KEY", "sk-kimi-test-not-real")
    monkeypatch.setenv("DEEPSEEK_API_KEY", _GENERATED_KEY_1)
    monkeypatch.delenv("FACTORY_CODE_PROVIDER", raising=False)
    monkeypatch.delenv("KIMI_CODE_MODEL", raising=False)
    monkeypatch.delenv("KIMI_CODE_MODEL_ID", raising=False)
    result = ensure_code_cli_credentials()
    assert result["ok"] is True
    dest = tmp_path / "kimi-home" / "config.toml"
    text = dest.read_text(encoding="utf-8")
    assert "[providers.deepseek]" in text
    assert "[providers.kimi]" in text
    assert "sk-kimi-test-not-real" in text
    assert f'default_model = "{DEFAULT_DEEPSEEK_MODEL}"' in text
    assert result["deepseek"]["ok"] is True


def test_probe_deepseek_credentials_missing(tmp_path, monkeypatch):
    script = _fake_kimi(tmp_path)
    _require_cli(monkeypatch)
    monkeypatch.setenv("FACTORY_CODE_CLI", str(script))
    monkeypatch.setenv("FACTORY_CODE_PROVIDER", "deepseek")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    probe = probe_code_cli()
    assert probe["available"] is True
    assert probe["provider"] == "deepseek"
    assert probe["requires_deepseek_credentials"] is True
    assert probe["deepseek_key_present"] is False
    assert probe["blocker"] == NAMED_BLOCKER_CLI_CREDS
    assert "DEEPSEEK_API_KEY" in probe["error"]
    assert "pilot_zip" not in probe["error"]
    assert "Claude Code" in probe["error"]


def test_probe_deepseek_ready(tmp_path, monkeypatch):
    script = _fake_kimi(tmp_path)
    _require_cli(monkeypatch)
    monkeypatch.setenv("FACTORY_CODE_CLI", str(script))
    monkeypatch.setenv("DEEPSEEK_API_KEY", _GENERATED_KEY_1)
    probe = probe_code_cli()
    assert probe["available"] is True
    assert probe["provider"] == "deepseek"
    assert probe["requires_deepseek_credentials"] is True
    assert probe["requires_kimi_credentials"] is False
    assert probe["deepseek_key_present"] is True
    assert probe["default_model"] == DEFAULT_DEEPSEEK_MODEL
    assert probe["default_model_configured"] is True
    assert "blocker" not in probe
    raise_if_cli_session_unready()


def test_probe_deepseek_binary_missing(tmp_path, monkeypatch):
    _require_cli(monkeypatch)
    monkeypatch.setenv("FACTORY_CODE_CLI", str(tmp_path / "no-such-kimi"))
    monkeypatch.setenv("DEEPSEEK_API_KEY", _GENERATED_KEY_1)
    probe = probe_code_cli()
    assert probe["available"] is False
    assert probe["blocker"] == NAMED_BLOCKER_CLI
    assert "DEEPSEEK_API_KEY" in probe["error"]


def test_dispatch_deepseek_uses_prompt_and_kimi_model_env(tmp_path, monkeypatch):
    argv_log = tmp_path / "argv.log"
    script = tmp_path / "kimi"
    script.write_text(
        "#!/bin/sh\n"
        '{ printf "%s\\n" "$0" "$@"; '
        'printf "MODEL=%s\\n" "$KIMI_MODEL_NAME"; '
        'printf "BASE=%s\\n" "$KIMI_MODEL_BASE_URL"; '
        'printf "TYPE=%s\\n" "$KIMI_MODEL_PROVIDER_TYPE"; '
        'printf "TOKEN_SET=%s\\n" "${KIMI_MODEL_API_KEY:+yes}"; '
        'printf "DEEPSEEK_SET=%s\\n" "${DEEPSEEK_API_KEY:+yes}"; '
        'printf "ANTHROPIC_BASE=%s\\n" "$ANTHROPIC_BASE_URL"; '
        'printf "STDIN_BYTES=%s\\n" "$(wc -c)"; '
        '} > "$CODE_CLI_ARGV_LOG"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    monkeypatch.setenv("FACTORY_CODE_CLI", str(script))
    monkeypatch.setenv("DEEPSEEK_API_KEY", _GENERATED_KEY_1)
    monkeypatch.setenv("CODE_CLI_ARGV_LOG", str(argv_log))
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "1")
    monkeypatch.delenv("FACTORY_BRIEF_HTTP_ONESHOT", raising=False)
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
    assert oneshot == []
    logged = argv_log.read_text(encoding="utf-8")
    assert "--prompt" in logged
    assert "--print" not in logged
    assert "--dangerously-skip-permissions" not in logged
    assert "@docs/coder_brief.md" not in logged
    assert "TARGET" in logged or "STEP 0" in logged or "INVENTORY" in logged
    assert f"MODEL={DEFAULT_DEEPSEEK_MODEL}" in logged
    assert f"BASE={DEEPSEEK_OPENAI_BASE_URL}" in logged
    assert "TYPE=openai" in logged
    assert "TOKEN_SET=yes" in logged
    assert "DEEPSEEK_SET=yes" in logged
    assert "ANTHROPIC_BASE=\n" in logged or logged.endswith("ANTHROPIC_BASE=")
    stdin_line = [ln for ln in logged.splitlines() if ln.startswith("STDIN_BYTES=")]
    assert stdin_line, logged
    assert int(stdin_line[0].split("=", 1)[1]) > 0
    receipt = json.loads(
        (tmp_path / "build" / "docs" / "coder_receipt.json").read_text(encoding="utf-8")
    )
    assert receipt["ok"] is True
    assert "pilot_zip" not in json.dumps(receipt)
    assert "ANTHROPIC_BASE_URL" not in __import__("os").environ


def test_default_deepseek_model_is_catalog_id_not_claude_opus():
    assert DEFAULT_DEEPSEEK_MODEL.startswith("deepseek-")
    assert DEFAULT_DEEPSEEK_MODEL != LEGACY_CLAUDE_OPUS_MODEL
    assert DEFAULT_DEEPSEEK_MODEL != REJECTED_DEEPSEEK_CLAUDE_MODEL
    assert "[" not in DEFAULT_DEEPSEEK_MODEL
    # Stripping the context suffix yields the bare catalog id, whatever the
    # default happens to be.
    assert normalize_deepseek_model(REJECTED_DEEPSEEK_CLAUDE_MODEL) == (
        REJECTED_DEEPSEEK_BARE_MODEL
    )
    assert normalize_deepseek_model("deepseek-v4-pro") == "deepseek-v4-pro"
    assert normalize_deepseek_model(LEGACY_CLAUDE_OPUS_MODEL) == DEFAULT_DEEPSEEK_MODEL
    assert normalize_deepseek_model("claude-haiku-4-5") == DEFAULT_DEEPSEEK_FLASH_MODEL


def test_leftover_anthropic_model_maps_back_to_deepseek_catalog(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL", LEGACY_CLAUDE_OPUS_MODEL)
    monkeypatch.delenv("DEEPSEEK_CODE_MODEL", raising=False)
    assert deepseek_code_model() == DEFAULT_DEEPSEEK_MODEL
    env = deepseek_cli_environ(_GENERATED_KEY_1)
    assert env["KIMI_MODEL_NAME"] == DEFAULT_DEEPSEEK_MODEL
    assert env["DEEPSEEK_CODE_MODEL"] == DEFAULT_DEEPSEEK_MODEL
    assert LEGACY_CLAUDE_OPUS_MODEL not in env.values()
    assert REJECTED_DEEPSEEK_CLAUDE_MODEL not in env.values()


def test_classify_deepseek_402_is_billing():
    blocker, detail = classify_cli_exit(
        1,
        "HTTP 402 from https://api.deepseek.com",
    )
    assert blocker == NAMED_BLOCKER_CLI_BILLING
    assert "FACTORY_CODE_CLI_BILLING" in detail
    assert "≥2h" in detail or "2h" in detail


def test_classify_unrecognized_model_is_model_denied():
    live = (
        '[claude-code:unrecognized_model] '
        '{"model":"deepseek-v4-pro[1m]","query_source":"sdk"}'
    )
    blocker, detail = classify_cli_exit(1, live)
    assert blocker == NAMED_BLOCKER_CLI_MODEL_DENIED
    assert blocker != NAMED_BLOCKER_CLI_FAILED
    assert NAMED_BLOCKER_CLI_MODEL_DENIED in detail
    assert NAMED_BLOCKER_CLI_FAILED in detail
    assert REJECTED_DEEPSEEK_CLAUDE_MODEL in detail
    assert DEEPSEEK_CODE_MODEL_ENV in detail
    assert DEFAULT_DEEPSEEK_MODEL in detail
    assert "OpenRouter" in detail
    assert "not the DeepSeek vehicle" in detail
    billing_wins, _ = classify_cli_exit(
        1,
        live + "\nHTTP 402 from https://api.deepseek.com",
    )
    assert billing_wins == NAMED_BLOCKER_CLI_BILLING


def test_kimi_prompt_argv_shape():
    brief = (
        "# TARGET\nResidential Lettings — implement the gated Factory C-BRIEF.\n"
        "## STEP 0 INVENTORY\nlettings_core GENERATE\n"
    )
    argv = kimi_prompt_argv("/usr/local/bin/kimi", brief, model=DEFAULT_DEEPSEEK_MODEL)
    assert argv[0] == "/usr/local/bin/kimi"
    assert argv[1] == "--prompt"
    assert argv[2] == brief.strip()
    assert "--add-dir" in argv
    assert "--model" in argv
    assert DEFAULT_DEEPSEEK_MODEL in argv
    assert "@docs/coder_brief.md" not in argv
    assert "--print" not in argv
    logged = kimi_prompt_log_argv("/usr/local/bin/kimi", len(brief), model=DEFAULT_DEEPSEEK_MODEL)
    assert "<docs/coder_brief.md" in logged[2]


def test_kimi_prompt_argv_rejects_empty_and_bare_at_path():
    for empty in ("", "   ", "@docs/coder_brief.md", "@docs/coder_brief.md\n"):
        with pytest.raises(KimiPromptEmpty):
            kimi_prompt_argv("/usr/local/bin/kimi", empty)
        with pytest.raises(KimiPromptEmpty):
            kimi_prompt_text(empty)


def test_kimi_prompt_argv_large_brief_keeps_nonempty_prompt_arg():
    brief = "# TARGET\n" + ("x" * 90_000)
    argv = kimi_prompt_argv("/usr/local/bin/kimi", brief)
    assert argv[1] == "--prompt"
    assert argv[2] == KIMI_PROMPT_STDIN_INSTRUCTION
    assert argv[2].strip()
    assert "@docs/coder_brief.md" not in argv


_STRICT_KIMI = textwrap.dedent(
    r"""
    #!/usr/bin/env python3
    import os
    import sys
    from pathlib import Path

    argv = sys.argv[1:]
    prompt = ""
    saw_prompt = False
    i = 0
    while i < len(argv):
        if argv[i] in ("--prompt", "-p"):
            saw_prompt = True
            if i + 1 < len(argv) and not argv[i + 1].startswith("-"):
                prompt = argv[i + 1]
                i += 2
                continue
        i += 1
    stdin_data = sys.stdin.read() if not sys.stdin.isatty() else ""

    def fail() -> None:
        print(
            "Error: Input must be provided either through stdin or as a "
            "prompt argument when using --prompt"
        )
        raise SystemExit(1)

    if not saw_prompt:
        fail()
    if prompt.startswith("@") and "/" in prompt and "\n" not in prompt:
        if not stdin_data.strip():
            fail()
    if not prompt.strip() and not stdin_data.strip():
        fail()
    body = f"{prompt}\n{stdin_data}"
    if not any(tok in body for tok in ("TARGET", "STEP 0", "INVENTORY")):
        fail()
    dest = os.environ.get("CODE_CLI_ARGV_LOG")
    if dest:
        Path(dest).write_text(
            "ARGC={0}\nPROMPT={1}\nPROMPT_BYTES={2}\nSTDIN_BYTES={3}\n"
            "MODEL={4}\nBASE={5}\nTYPE={6}\nTOKEN_SET={7}\n"
            "ANTHROPIC_SET={8}\nBODY_HEAD={9}\n".format(
                len(sys.argv),
                "yes" if saw_prompt else "no",
                len(prompt),
                len(stdin_data),
                os.environ.get("KIMI_MODEL_NAME", ""),
                os.environ.get("KIMI_MODEL_BASE_URL", ""),
                os.environ.get("KIMI_MODEL_PROVIDER_TYPE", ""),
                "yes" if os.environ.get("KIMI_MODEL_API_KEY") else "no",
                "yes" if os.environ.get("ANTHROPIC_AUTH_TOKEN") else "no",
                body[:240].replace("\n", " "),
            ),
            encoding="utf-8",
        )
    Path("docs").mkdir(parents=True, exist_ok=True)
    Path("docs/kimi_received_brief.txt").write_text(
        f"ok prompt={len(prompt)} stdin={len(stdin_data)}\n",
        encoding="utf-8",
    )
    raise SystemExit(0)
    """
).lstrip()


def _strict_kimi(tmp_path: Path) -> Path:
    script = tmp_path / "kimi"
    script.write_text(_STRICT_KIMI, encoding="utf-8")
    script.chmod(0o755)
    return script


def _arm_deepseek_cli(tmp_path, monkeypatch, script: Path | None = None) -> Path:
    cli = script or _fake_kimi(tmp_path)
    monkeypatch.setenv("FACTORY_CODE_CLI", str(cli))
    monkeypatch.setenv("FACTORY_CODE_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", _GENERATED_KEY_1)
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "1")
    monkeypatch.delenv("FACTORY_BRIEF_HTTP_ONESHOT", raising=False)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    return cli


def test_dispatch_deepseek_strict_kimi_receives_brief(tmp_path, monkeypatch):
    argv_log = tmp_path / "argv.log"
    script = _strict_kimi(tmp_path)
    _arm_deepseek_cli(tmp_path, monkeypatch, script)
    monkeypatch.setenv("CODE_CLI_ARGV_LOG", str(argv_log))
    monkeypatch.delenv("FACTORY_BRIEF_REQUIRE_CLI", raising=False)
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
    assert oneshot == []
    logged = argv_log.read_text(encoding="utf-8")
    assert "PROMPT=yes" in logged
    assert "TOKEN_SET=yes" in logged
    assert "ANTHROPIC_SET=no" in logged
    assert f"MODEL={DEFAULT_DEEPSEEK_MODEL}" in logged
    assert f"BASE={DEEPSEEK_OPENAI_BASE_URL}" in logged
    assert "TYPE=openai" in logged
    prompt_bytes = int(
        [ln for ln in logged.splitlines() if ln.startswith("PROMPT_BYTES=")][0].split(
            "=", 1
        )[1]
    )
    stdin_bytes = int(
        [ln for ln in logged.splitlines() if ln.startswith("STDIN_BYTES=")][0].split(
            "=", 1
        )[1]
    )
    assert prompt_bytes > 0
    assert stdin_bytes > 0
    received = tmp_path / "build" / "docs" / "kimi_received_brief.txt"
    assert received.is_file(), "mock kimi must write a workspace artifact"
    session_log = (tmp_path / "build" / "docs" / "coder_session.log").read_text(
        encoding="utf-8"
    )
    assert "--prompt" in session_log
    assert "@docs/coder_brief.md" not in session_log.split("$", 1)[-1].split("\n", 1)[0]
    assert "ANTHROPIC_BASE_URL" not in __import__("os").environ


def test_deepseek_ready_requires_cli_even_when_env_test(tmp_path, monkeypatch):
    _arm_deepseek_cli(tmp_path, monkeypatch)
    monkeypatch.setenv("ENV", "test")
    monkeypatch.delenv("FACTORY_BRIEF_REQUIRE_CLI", raising=False)
    monkeypatch.delenv("FACTORY_BRIEF_DISPATCH", raising=False)
    assert deepseek_cli_ready() is True
    assert brief_requires_cli() is True
    raise_if_cli_session_unready()


def test_deepseek_ready_requires_cli_even_when_require_cli_off(tmp_path, monkeypatch):
    _arm_deepseek_cli(tmp_path, monkeypatch)
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("FACTORY_BRIEF_REQUIRE_CLI", "0")
    assert deepseek_cli_ready() is True
    assert brief_requires_cli() is True
    raise_if_cli_session_unready()


def test_deepseek_ready_forces_brief_dispatch(tmp_path, monkeypatch):
    _arm_deepseek_cli(tmp_path, monkeypatch)
    monkeypatch.setenv("FACTORY_BRIEF_DISPATCH", "0")
    assert brief_dispatch_enabled() is True
    assert brief_requires_cli() is True


def test_dispatch_deepseek_ready_never_calls_factory_llm_even_with_generate_gap(
    tmp_path, monkeypatch
):
    _arm_deepseek_cli(tmp_path, monkeypatch)
    monkeypatch.delenv("FACTORY_BRIEF_REQUIRE_CLI", raising=False)
    oneshot = []
    monkeypatch.setattr(
        "app.factory.coder.generate_from_compiled_brief",
        lambda **kw: oneshot.append(kw)
        or {"specs": {}, "handlers": {}, "model": "minimax/minimax-m3:free"},
    )

    class _Gap:
        capability_id = "lettings_core"
        block_ids = ()
        strategy = "GENERATE"
        notes = "gap"

    class _Plan:
        capabilities = (_Gap(),)

    ctx = _ctx(tmp_path)
    ctx.plan = _Plan()
    compiled = compile_brief(ctx.blueprint, ctx.plan, store_ids={"analytics"})
    assert any(item.is_gap for item in compiled.inventory)
    ctx.workspace.write_text(Path("docs") / "coder_brief.md", compiled.text)
    ctx.workspace.write_text(Path("docs") / "coder_session.log", "")
    result = dispatch_compiled_brief(ctx, compiled)
    assert result.ok, result.detail
    assert result.via == "cli"
    assert result.factory_llm_generate_fallthrough is False
    assert oneshot == []
    assert "ANTHROPIC_BASE_URL" not in __import__("os").environ


def test_dispatch_deepseek_billing_does_not_openrouter_fallthrough(
    tmp_path, monkeypatch
):
    script = tmp_path / "kimi"
    script.write_text(
        "#!/bin/sh\n"
        "echo 'HTTP 429 from https://api.deepseek.com: insufficient quota'\n"
        "exit 1\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    _arm_deepseek_cli(tmp_path, monkeypatch, script)
    monkeypatch.setenv("FACTORY_BRIEF_REQUIRE_CLI", "1")
    oneshot = []
    monkeypatch.setattr(
        "app.factory.coder.generate_from_compiled_brief",
        lambda **kw: oneshot.append(kw)
        or {"specs": {}, "handlers": {}, "model": "minimax/minimax-m3:free"},
    )

    class _Gap:
        capability_id = "lettings_core"
        block_ids = ()
        strategy = "GENERATE"
        notes = "gap"

    class _Plan:
        capabilities = (_Gap(),)

    ctx = _ctx(tmp_path)
    ctx.plan = _Plan()
    compiled = compile_brief(ctx.blueprint, ctx.plan, store_ids={"analytics"})
    ctx.workspace.write_text(Path("docs") / "coder_brief.md", compiled.text)
    ctx.workspace.write_text(Path("docs") / "coder_session.log", "")
    result = dispatch_compiled_brief(ctx, compiled)
    assert result.ok is False
    assert result.via == "cli"
    assert result.blocker == NAMED_BLOCKER_CLI_BILLING
    assert deepseek_cli_ready() is True
    assert should_factory_llm_generate_gaps(compiled, result) is False
    assert result.factory_llm_generate_fallthrough is False
    assert result.generate_persist_ids == []
    assert oneshot == []
    assert "ANTHROPIC_BASE_URL" not in __import__("os").environ


def test_leftover_47s_wall_remaps_when_deepseek_ready(tmp_path, monkeypatch):
    from app.factory.build_jobs import _wall_clock_s

    _arm_deepseek_cli(tmp_path, monkeypatch)
    monkeypatch.setenv("FACTORY_BUILD_WALL_CLOCK_S", "47")
    assert deepseek_cli_ready() is True
    assert _wall_clock_s() == 1800.0


def test_explicit_2h_wall_still_honoured_with_deepseek(tmp_path, monkeypatch):
    from app.factory.build_jobs import _wall_clock_s

    _arm_deepseek_cli(tmp_path, monkeypatch)
    monkeypatch.setenv("FACTORY_BUILD_WALL_CLOCK_S", "7200")
    assert _wall_clock_s() == 7200.0


def test_collector_skips_factory_llm_when_deepseek_ready(tmp_path, monkeypatch):
    from app.factory.build.authority import BuildRole
    from app.factory.build.roles import run_collector
    from app.factory.build.roles_models import RoleContext
    from app.factory.build.workspace import RoleWorkspace

    _arm_deepseek_cli(tmp_path, monkeypatch)
    called = []
    monkeypatch.setattr(
        "app.factory.coder.review_capability_bindings",
        lambda **kw: called.append(kw)
        or {"reviews": [], "model": "minimax/minimax-m3:free"},
    )
    notes = []

    class _Cap:
        capability_id = "lettings_core"
        block_ids = ("persist",)
        notes = "core"

    class _Plan:
        capabilities = (_Cap(),)

    ctx = RoleContext(
        role=BuildRole.COLLECTOR,
        workspace=RoleWorkspace(BuildRole.COLLECTOR, tmp_path / "build"),
        blueprint=_Blueprint(),
        plan=_Plan(),
        state={},
        progress=lambda detail, payload: notes.append(detail),
    )
    result = run_collector(ctx)
    assert result.ok
    assert called == [], "DeepSeek-ready COLLECTOR must not call factory LLM"
    assert any("FACTORY_CODE_CLI" in n and "DeepSeek" in n for n in notes)


LETTINGS_STORE = {
    "analytics",
    "team",
    "workflow",
    "notification",
    "document_engine",
}


class _ReuseCap:
    def __init__(self, cid, bids, strategy="REUSE"):
        self.capability_id = cid
        self.block_ids = list(bids)
        self.strategy = strategy
        self.notes = cid


class _LettingsReusePlan:
    capabilities = (
        _ReuseCap("unit_registry_and_vacancy_tracking", ["analytics"], "REUSE"),
        _ReuseCap(
            "viewing_management", ["team", "workflow", "notification"], "COMPOSE"
        ),
        _ReuseCap("maintenance_issue_tracking", ["team"], "REUSE"),
        _ReuseCap(
            "tenancy_application_pipeline", ["team", "document_engine"], "COMPOSE"
        ),
    )


def test_dispatch_deepseek_ready_all_reuse_compose_uses_cli(tmp_path, monkeypatch):
    argv_log = tmp_path / "argv.log"
    script = tmp_path / "kimi"
    script.write_text(
        "#!/bin/sh\n"
        '{ printf "%s\\n" "$0" "$@"; '
        'printf "STDIN_BYTES=%s\\n" "$(wc -c)"; '
        '} > "$CODE_CLI_ARGV_LOG"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    _arm_deepseek_cli(tmp_path, monkeypatch, script)
    monkeypatch.setenv("CODE_CLI_ARGV_LOG", str(argv_log))
    monkeypatch.delenv("FACTORY_BRIEF_REQUIRE_CLI", raising=False)
    oneshot = []
    monkeypatch.setattr(
        "app.factory.coder.generate_from_compiled_brief",
        lambda **kw: oneshot.append(kw)
        or {"specs": {}, "handlers": {}, "model": "minimax/minimax-m3:free"},
    )
    ctx = _ctx(tmp_path)
    ctx.plan = _LettingsReusePlan()
    compiled = compile_brief(
        ctx.blueprint, ctx.plan, store_ids=LETTINGS_STORE
    )
    assert inventory_gap_ids(compiled) == []
    assert all(not item.is_gap for item in compiled.inventory)
    assert "Do not skip the CLI because inventory_gaps is empty" in compiled.text
    ctx.workspace.write_text(Path("docs") / "coder_brief.md", compiled.text)
    ctx.workspace.write_text(Path("docs") / "coder_session.log", "")
    result = dispatch_compiled_brief(ctx, compiled)
    assert result.ok, result.detail
    assert result.via == "cli"
    assert oneshot == []
    logged = argv_log.read_text(encoding="utf-8")
    assert "--prompt" in logged
    assert "--print" not in logged
    assert "@docs/coder_brief.md" not in logged
    stdin_line = [ln for ln in logged.splitlines() if ln.startswith("STDIN_BYTES=")]
    assert stdin_line and int(stdin_line[0].split("=", 1)[1]) > 0
    receipt = json.loads(
        (tmp_path / "build" / "docs" / "coder_receipt.json").read_text(encoding="utf-8")
    )
    assert receipt["via"] == "cli"
    assert "pilot_zip" not in json.dumps(receipt)


def test_writer_all_reuse_compose_dispatches_cli_when_deepseek_ready(
    tmp_path, monkeypatch
):
    from app.factory.build.roles import run_writer

    script = tmp_path / "kimi"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)
    _arm_deepseek_cli(tmp_path, monkeypatch, script)
    monkeypatch.delenv("FACTORY_BRIEF_REQUIRE_CLI", raising=False)
    oneshot = []
    monkeypatch.setattr(
        "app.factory.coder.generate_from_compiled_brief",
        lambda **kw: oneshot.append(kw)
        or {"specs": {}, "handlers": {}, "model": "minimax/minimax-m3:free"},
    )
    plan = _LettingsReusePlan()
    compiled = compile_brief(_Blueprint(), plan, store_ids=LETTINGS_STORE)
    assert inventory_gap_ids(compiled) == []
    monkeypatch.setattr(
        "app.factory.build.brief_compiler.compile_brief_from_ctx",
        lambda _ctx: compiled,
    )
    dest = tmp_path / "dest"
    dest.mkdir()
    ws = RoleWorkspace(BuildRole.WRITER, dest)
    with pytest.raises(RoleError) as exc:
        run_writer(
            RoleContext(
                role=BuildRole.WRITER,
                workspace=ws,
                blueprint=_Blueprint(),
                plan=plan,
                state={
                    "resolved_blocks": tuple(LETTINGS_STORE),
                    "vendored_blocks": tuple(LETTINGS_STORE),
                },
            )
        )
    # 0.5: a CLI exit-0 with zero harvest is zero coding-agent artifacts
    # (never cbrief authorship) -- the writer refuses with the named
    # reason; the receipt evidence still lands before it.
    assert "writer_no_output" in str(exc.value)
    assert oneshot == []
    receipt = json.loads(
        (dest / "docs" / "coder_receipt.json").read_text(encoding="utf-8")
    )
    assert receipt["via"] == "cli"
    assert receipt.get("factory_llm_generate_fallthrough") is False
    assert "ANTHROPIC_BASE_URL" not in __import__("os").environ


def test_budget_inspect_does_not_success_thin_stubs_before_cli_when_deepseek_ready(
    tmp_path, monkeypatch
):
    from app.factory.build.authority import BuildRole
    from app.factory.build.budget_inspect import inspect_build, inspect_decision
    from app.factory.build.ledger import BuildLedger, EventKind
    from app.factory.build.runner import Outcome, RoleRunner
    from app.factory.blueprint import load_blueprint

    _arm_deepseek_cli(tmp_path, monkeypatch)
    assert deepseek_cli_ready() is True
    out = tmp_path / "build"
    out.mkdir()
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id="residential-lettings", inputs_hash="abc")
    for cap in (
        "unit_registry_and_vacancy_tracking",
        "viewing_management",
        "maintenance_issue_tracking",
        "tenancy_application_pipeline",
    ):
        ledger.append(
            EventKind.NOTE,
            role=BuildRole.WRITER,
            detail=f"wrote handler {cap} (deterministic contract template)",
            payload={
                "stage": "handlers",
                "capability": cap,
                "source": "deterministic contract template",
            },
        )
    snap = inspect_build(ledger)
    assert snap["agent_written"] == 0
    assert snap["templated"] == 4
    assert snap["stub_rate"] == 1.0
    assert snap["cli_attempted"] is False
    decided = inspect_decision(
        elapsed_s=8.0,
        current_wall_s=1800.0,
        snapshot=snap,
        stage="pilot_open",
    )
    assert decided["decision"] == "await_cli"
    assert decided["decision"] != "hard_stop"
    blocker = thin_stub_success_blocked(
        snapshot=snap, elapsed_s=8.0, ledger=ledger
    )
    assert blocker
    assert NAMED_BLOCKER_CLI_UNUSED in blocker

    root = Path(__file__).resolve().parents[3]
    runner = RoleRunner(
        load_blueprint(root / "blueprints/examples/runner_smoke.yaml"),
        out,
        ledger=ledger,
    )
    runner._run_started = runner.clock()
    runner.state["brief_dispatch"] = {"via": "skipped"}
    outcome = runner._finish(
        Outcome.SUCCESS,
        "CODE PASS — suite; PRODUCT PASS — persist; STORE PASS — ops",
    )
    assert outcome.ok is False
    assert outcome.outcome.value == "FAILED_ROLE_ERROR"
    assert NAMED_BLOCKER_CLI_UNUSED in (outcome.detail or "")
    terminal = ledger.terminal_event()
    assert terminal is not None
    assert terminal.kind is EventKind.RUN_FAILED
    assert "pilot_zip" not in (outcome.detail or "")


def test_dispatch_exit_0_zero_harvest_is_not_cbrief_authorship(
    tmp_path, monkeypatch
):
    """kimi exit 0 with no handlers is via=cli, not successful C-BRIEF authorship."""
    _arm_deepseek_cli(tmp_path, monkeypatch)
    monkeypatch.delenv("FACTORY_BRIEF_REQUIRE_CLI", raising=False)
    oneshot = []
    monkeypatch.setattr(
        "app.factory.coder.generate_from_compiled_brief",
        lambda **kw: oneshot.append(kw)
        or {"specs": {}, "handlers": {}, "model": "minimax/minimax-m3:free"},
    )
    ctx = _ctx(tmp_path)
    ctx.plan = _LettingsReusePlan()
    compiled = compile_brief(
        ctx.blueprint, ctx.plan, store_ids=LETTINGS_STORE
    )
    assert inventory_gap_ids(compiled) == []
    ctx.workspace.write_text(Path("docs") / "coder_brief.md", compiled.text)
    ctx.workspace.write_text(Path("docs") / "coder_session.log", "")
    result = dispatch_compiled_brief(ctx, compiled)
    assert result.via == "cli"
    assert result.ok, result.detail
    assert oneshot == []
    assert list(result.cli_authored_ids) == []
    assert result.blocker == NAMED_BLOCKER_CLI_NO_AUTHORSHIP
    assert NAMED_BLOCKER_CLI_NO_AUTHORSHIP in result.detail
    receipt = json.loads(
        (tmp_path / "build" / "docs" / "coder_receipt.json").read_text(
            encoding="utf-8"
        )
    )
    assert receipt["via"] == "cli"
    assert receipt.get("cli_authored_ids") == []
    assert "pilot_zip" not in json.dumps(receipt)


def test_cli_exit_0_zero_harvest_does_not_finish_success_when_deepseek_ready(
    tmp_path, monkeypatch
):
    from app.factory.build.authority import BuildRole
    from app.factory.build.budget_inspect import STAGE_1_S, inspect_build
    from app.factory.build.ledger import BuildLedger, EventKind
    from app.factory.build.runner import Outcome, RoleRunner
    from app.factory.blueprint import load_blueprint

    _arm_deepseek_cli(tmp_path, monkeypatch)
    assert deepseek_cli_ready() is True
    out = tmp_path / "build"
    out.mkdir()
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id="residential-lettings", inputs_hash="abc")
    ledger.append(
        EventKind.NOTE,
        role=BuildRole.WRITER,
        detail="dispatching compiled brief via FACTORY_CODE_CLI (kimi)",
        payload={"stage": "dispatch", "source": "coder CLI", "done": 0, "total": 1},
    )
    for cap in (
        "unit_registry_and_vacancy_tracking",
        "viewing_management",
        "maintenance_issue_tracking",
        "tenancy_application_pipeline",
    ):
        ledger.append(
            EventKind.NOTE,
            role=BuildRole.WRITER,
            detail=f"wrote handler {cap} (factory-grounded persist)",
            payload={
                "stage": "handlers",
                "capability": cap,
                "source": "factory-grounded persist",
            },
        )
    snap = inspect_build(ledger)
    assert snap["agent_written"] >= 1
    assert snap["cli_or_llm_written"] == 0
    assert snap["cli_attempted"] is True
    blocker = thin_stub_success_blocked(
        snapshot=snap,
        elapsed_s=STAGE_1_S + 30.0,
        state={
            "brief_dispatch": {
                "via": "cli",
                "ok": True,
                "cli_authored_ids": [],
                "handler_ids": [],
            }
        },
        ledger=ledger,
    )
    assert blocker
    assert NAMED_BLOCKER_CLI_NO_AUTHORSHIP in blocker
    assert "pilot_zip" not in blocker

    root = Path(__file__).resolve().parents[3]
    runner = RoleRunner(
        load_blueprint(root / "blueprints/examples/runner_smoke.yaml"),
        out,
        ledger=ledger,
    )
    runner._run_started = runner.clock() - (STAGE_1_S + 30.0)
    runner.state["brief_dispatch"] = {
        "via": "cli",
        "ok": True,
        "cli_authored_ids": [],
        "handler_ids": [],
        "kept_handler_ids": [
            "unit_registry_and_vacancy_tracking",
            "viewing_management",
        ],
    }
    outcome = runner._finish(
        Outcome.SUCCESS,
        "CODE PASS — suite; PRODUCT PASS — persist; STORE PASS — ops",
    )
    assert outcome.ok is False
    assert outcome.outcome.value == "FAILED_ROLE_ERROR"
    assert NAMED_BLOCKER_CLI_NO_AUTHORSHIP in (outcome.detail or "")
    terminal = ledger.terminal_event()
    assert terminal is not None
    assert terminal.kind is EventKind.RUN_FAILED
    assert "pilot_zip" not in (outcome.detail or "")


def test_thin_stub_blocked_after_stage_1_wall_when_cli_ready_zero_writes(
    tmp_path, monkeypatch
):
    """Mutation: after-wall thin stubs must not return None when CLI is ready."""
    from app.factory.build.budget_inspect import STAGE_1_S

    _arm_deepseek_cli(tmp_path, monkeypatch)
    snap = {
        "agent_written": 0,
        "cli_or_llm_written": 0,
        "templated": 4,
        "stub_rate": 1.0,
        "cli_attempted": True,
    }
    blocker = thin_stub_success_blocked(
        snapshot=snap,
        elapsed_s=STAGE_1_S + 10.0,
        state={"brief_dispatch": {"via": "cli", "ok": True, "cli_authored_ids": []}},
    )
    assert blocker
    assert (
        NAMED_BLOCKER_CLI_NO_AUTHORSHIP in blocker
        or NAMED_BLOCKER_CLI_UNUSED in blocker
    )
    assert thin_stub_success_blocked(
        snapshot=snap,
        elapsed_s=STAGE_1_S + 10.0,
        state={"brief_dispatch": {"via": "cli", "ok": True, "cli_authored_ids": []}},
    ) is not None


def test_hung_killed_by_wall_is_not_unused_and_refuses_thin_success(
    tmp_path, monkeypatch
):
    from app.factory.build.ledger import BuildLedger
    from app.factory.build.runner import Outcome, RoleRunner
    from app.factory.blueprint import load_blueprint

    _arm_deepseek_cli(tmp_path, monkeypatch)
    snap = {
        "agent_written": 0,
        "cli_or_llm_written": 0,
        "templated": 4,
        "stub_rate": 1.0,
        "cli_attempted": True,
        "cli_in_flight": False,
        "cli_finished": True,
    }
    blocker = thin_stub_success_blocked(
        snapshot=snap,
        elapsed_s=2700.0,
        state={
            "brief_dispatch": {
                "via": "cli",
                "ok": False,
                "blocker": NAMED_BLOCKER_CLI_HUNG_KILLED_BY_WALL,
                "cli_authored_ids": [],
            }
        },
    )
    assert blocker
    assert NAMED_BLOCKER_CLI_HUNG_KILLED_BY_WALL in blocker
    assert NAMED_BLOCKER_CLI_UNUSED not in blocker

    out = tmp_path / "build"
    out.mkdir(exist_ok=True)
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id="vetcare-hub", inputs_hash="abc")
    root = Path(__file__).resolve().parents[3]
    runner = RoleRunner(
        load_blueprint(root / "blueprints/examples/runner_smoke.yaml"),
        out,
        ledger=ledger,
    )
    runner._run_started = runner.clock()
    runner.state["brief_dispatch"] = {
        "via": "cli",
        "ok": False,
        "blocker": NAMED_BLOCKER_CLI_HUNG_KILLED_BY_WALL,
        "cli_authored_ids": [],
    }
    outcome = runner._finish(
        Outcome.SUCCESS,
        "CODE PASS — suite; PRODUCT PASS — persist; STORE PASS — ops",
    )
    assert outcome.ok is False
    assert NAMED_BLOCKER_CLI_HUNG_KILLED_BY_WALL in (outcome.detail or "")
    assert "pilot_zip" not in (outcome.detail or "")


def test_partial_thin_authorship_refuses_success_when_cli_ready(tmp_path, monkeypatch):
    """Unknown n_required keeps need=5; 3 of 4 required still refuses."""
    from app.factory.build.ledger import BuildLedger
    from app.factory.build.runner import Outcome, RoleRunner
    from app.factory.blueprint import load_blueprint

    _arm_deepseek_cli(tmp_path, monkeypatch)
    snap = {
        "agent_written": 3,
        "cli_or_llm_written": 3,
        "templated": 21,
        "stub_rate": 0.875,
        "cli_attempted": True,
    }
    blocker = thin_stub_success_blocked(
        snapshot=snap,
        elapsed_s=120.0,
        state={
            "brief_dispatch": {
                "via": "cli",
                "ok": True,
                "cli_authored_ids": [
                    "audit",
                    "vetcare_hub_veterinary_core",
                    "workflow",
                ],
            }
        },
    )
    assert blocker
    assert NAMED_BLOCKER_CLI_THIN_AUTHORSHIP in blocker
    assert NAMED_BLOCKER_CLI_NO_AUTHORSHIP not in blocker

    lettings_ids = [
        "unit_registry_and_vacancy_tracking",
        "viewing_management",
        "maintenance_issue_tracking",
        "tenancy_application_pipeline",
    ]
    thin_four = thin_stub_success_blocked(
        snapshot={**snap, "n_required": 4, "agent_written": 3, "cli_or_llm_written": 3},
        elapsed_s=120.0,
        state={
            "n_required": 4,
            "brief_dispatch": {
                "via": "cli",
                "ok": True,
                "cli_authored_ids": lettings_ids[:3],
            },
        },
    )
    assert thin_four
    assert "need ≥4" in thin_four
    full_four = thin_stub_success_blocked(
        snapshot={
            "agent_written": 4,
            "cli_or_llm_written": 4,
            "templated": 10,
            "stub_rate": 0.7,
            "cli_attempted": True,
            "n_required": 4,
        },
        elapsed_s=120.0,
        state={
            "n_required": 4,
            "brief_dispatch": {
                "via": "cli",
                "ok": True,
                "cli_authored_ids": lettings_ids,
            },
        },
    )
    assert full_four is None

    out = tmp_path / "build"
    out.mkdir(exist_ok=True)
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id="residential-lettings", inputs_hash="abc")
    root = Path(__file__).resolve().parents[3]
    runner = RoleRunner(
        load_blueprint(root / "blueprints/lettings/residential_lettings.v1.yaml"),
        out,
        ledger=ledger,
    )
    runner._run_started = runner.clock()
    runner.state["brief_dispatch"] = {
        "via": "cli",
        "ok": True,
        "cli_authored_ids": lettings_ids[:3],
    }
    outcome = runner._finish(
        Outcome.SUCCESS,
        "CODE PASS — suite; PRODUCT PASS — persist; STORE PASS — ops",
    )
    assert outcome.ok is False
    assert NAMED_BLOCKER_CLI_THIN_AUTHORSHIP in (outcome.detail or "")
    assert NAMED_BLOCKER_CLI_NO_AUTHORSHIP not in (outcome.detail or "")
    assert "need ≥4" in (outcome.detail or "")
    assert "pilot_zip" not in (outcome.detail or "")


def test_five_authored_actions_still_allow_success_when_cli_ready(tmp_path, monkeypatch):
    """Mutation: changing the floor to ignore ≥5 must fail this test."""
    _arm_deepseek_cli(tmp_path, monkeypatch)
    ids = ["audit", "workflow", "team", "document_engine", "validation"]
    blocker = thin_stub_success_blocked(
        snapshot={
            "agent_written": 5,
            "cli_or_llm_written": 5,
            "templated": 10,
            "stub_rate": 0.667,
            "cli_attempted": True,
        },
        elapsed_s=120.0,
        state={
            "brief_dispatch": {
                "via": "cli",
                "ok": True,
                "cli_authored_ids": ids,
                "handler_ids": ids,
            }
        },
    )
    assert blocker is None


def test_zero_written_stays_no_authorship_not_thin_floor(tmp_path, monkeypatch):
    """written=0 path must remain FACTORY_CODE_CLI_NO_AUTHORSHIP."""
    _arm_deepseek_cli(tmp_path, monkeypatch)
    blocker = thin_stub_success_blocked(
        snapshot={
            "agent_written": 0,
            "cli_or_llm_written": 0,
            "templated": 4,
            "stub_rate": 1.0,
            "cli_attempted": True,
        },
        elapsed_s=30.0,
        state={"brief_dispatch": {"via": "cli", "ok": True, "cli_authored_ids": []}},
    )
    assert blocker
    assert NAMED_BLOCKER_CLI_NO_AUTHORSHIP in blocker
    assert NAMED_BLOCKER_CLI_THIN_AUTHORSHIP not in blocker
