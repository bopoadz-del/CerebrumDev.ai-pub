"""CD-CLI-DIAG-1: empty-harvest classifier + staging coder_session.log retrieval.

Do not guess prompt/model/path here — classify from log text only.
Fail-closed FACTORY_CODE_CLI_NO_AUTHORSHIP is unchanged.
"""

from __future__ import annotations

from pathlib import Path

from app.factory.build.brief_compiler import compile_brief
from app.factory.build.coder_session import (
    CLI_EMPTY_DESCRIBED_NOT_WRITTEN,
    CLI_EMPTY_EMPTY_COMPLETION,
    CLI_EMPTY_WRITE_TARGETS_UNREAD,
    LOG_REL,
    NAMED_BLOCKER_CLI_NO_AUTHORSHIP,
    classify_cli_empty,
    dispatch_compiled_brief,
    read_log_tail,
    session_status,
)
from app.factory.build.ledger import BuildLedger
from app.factory.build_jobs import build_status
from tests.factory.test_deepseek_code_cli import _arm_deepseek_cli, _ctx


RUN9_STDOUT = (
    "run9 FACTORY_CODE_CLI stdout — STEP 0 inventory\n"
    "kimi --prompt dispatched; no harvested handlers\n"
)


def _described_not_written_log() -> str:
    return (
        "$ kimi --prompt [1840 chars]\n"
        "Here is the handler the brief asked for:\n"
        "\n"
        "```python\n"
        "CAPABILITY_ID = 'residential_lettings_core'\n"
        "def handle(payload):\n"
        "    return {'ok': True}\n"
        "```\n"
        "\n"
        "That satisfies STEP 0. No further work.\n"
    )


def _refused_log() -> str:
    return (
        "$ kimi --prompt [1840 chars]\n"
        "I cannot write application handlers for this request. "
        "I refuse to modify the workspace.\n"
    )


def _empty_completion_log() -> str:
    return "$ kimi --prompt [1840 chars]\n"


def _wrong_path_log() -> str:
    return (
        "$ kimi --prompt [1840 chars]\n"
        "● Write(tmp/scratch/handler.py)\n"
        "Wrote tmp/scratch/handler.py\n"
    )


def test_classify_cli_empty_described_not_written():
    reason = classify_cli_empty(_described_not_written_log())
    assert reason == CLI_EMPTY_DESCRIBED_NOT_WRITTEN
    assert reason == "described-not-written"


def test_a_prose_refusal_is_classified_by_what_the_session_did():
    """A refusal is the agent's words, which carry no structural signal: the
    session wrote nothing and described no code, so it is an empty
    completion -- and NO_AUTHORSHIP honesty is unchanged."""
    assert classify_cli_empty(_refused_log()) == CLI_EMPTY_EMPTY_COMPLETION


def test_classify_cli_empty_empty_completion():
    reason = classify_cli_empty(_empty_completion_log())
    assert reason == CLI_EMPTY_EMPTY_COMPLETION
    assert reason == "empty-completion"


def test_write_tool_prose_decides_nothing():
    """``Write(...)`` / ``Wrote ...`` is the CLI's prose, not a typed tool
    record: the session is classified by what its log structurally shows,
    and the detail says the write targets were not itemised."""
    assert classify_cli_empty(_wrong_path_log()) == CLI_EMPTY_EMPTY_COMPLETION
    assert "no typed tool record" in CLI_EMPTY_WRITE_TARGETS_UNREAD


def test_classify_cli_empty_blank_is_empty_completion():
    assert classify_cli_empty("") == CLI_EMPTY_EMPTY_COMPLETION
    assert classify_cli_empty("   \n") == CLI_EMPTY_EMPTY_COMPLETION


def test_classify_cli_empty_does_not_weaken_no_authorship():
    """Classifier names a reason; it is not a new honesty class."""
    for blob in (
        _described_not_written_log(),
        _refused_log(),
        _empty_completion_log(),
        _wrong_path_log(),
    ):
        reason = classify_cli_empty(blob)
        assert reason in {
            CLI_EMPTY_DESCRIBED_NOT_WRITTEN,
            CLI_EMPTY_EMPTY_COMPLETION,
        }
        assert NAMED_BLOCKER_CLI_NO_AUTHORSHIP not in reason


def _write_log(root: Path, text: str) -> Path:
    path = root / LOG_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_staging_writer_log_preferred_when_dest_empty(tmp_path):
    """Dest docs/coder_session.log empty → read WRITER staging sibling."""
    dest = tmp_path / "sess_5782f2264e0e4ff4"
    dest.mkdir()
    _write_log(dest, "")
    staging = dest.parent / f".{dest.name}.staging-writer"
    _write_log(staging, RUN9_STDOUT)

    tail = read_log_tail(dest)
    assert "run9 FACTORY_CODE_CLI stdout" in tail
    status = session_status(dest)
    assert "run9 FACTORY_CODE_CLI stdout" in status["coder_log"]
    assert status["coder_log_present"] is True


def test_staging_writer_log_used_when_dest_missing(tmp_path):
    dest = tmp_path / "build"
    dest.mkdir()
    staging = dest.parent / f".{dest.name}.staging-writer"
    _write_log(staging, RUN9_STDOUT)

    assert not (dest / LOG_REL).exists()
    assert "run9 FACTORY_CODE_CLI stdout" in read_log_tail(dest)
    status = session_status(dest)
    assert status["coder_log_present"] is True
    assert "run9" in status["coder_log"]


def test_newer_nonempty_dest_wins_over_older_staging(tmp_path):
    dest = tmp_path / "build"
    dest.mkdir()
    staging = dest.parent / f".{dest.name}.staging-writer"
    dest_log = _write_log(dest, "destination CLI stdout (newer)\n")
    staging_log = _write_log(staging, "older staging leftover\n")
    older = staging_log.stat().st_mtime - 120
    import os

    os.utime(staging_log, (older, older))
    os.utime(dest_log, (dest_log.stat().st_mtime + 1, dest_log.stat().st_mtime + 1))

    assert "destination CLI stdout" in read_log_tail(dest)
    assert "older staging leftover" not in read_log_tail(dest)


def test_build_status_surfaces_staging_coder_log_when_dest_empty(tmp_path):
    dest = tmp_path / "sess_5782f2264e0e4ff4"
    dest.mkdir()
    BuildLedger(dest / "build_ledger.jsonl").start_run(
        product_id="steward", inputs_hash="abc"
    )
    _write_log(dest, "")
    staging = dest.parent / f".{dest.name}.staging-writer"
    _write_log(staging, RUN9_STDOUT)

    status = build_status(dest)
    assert "run9 FACTORY_CODE_CLI stdout" in (status.get("coder_log") or "")
    assert status.get("coder_log_present") is True


def test_empty_harvest_folds_classify_reason_into_no_authorship(
    tmp_path, monkeypatch
):
    """Clean-exit empty harvest reads the log and names the empty class."""
    script = tmp_path / "kimi"
    script.write_text(
        "#!/bin/sh\ncat << 'EOF'\n" + _described_not_written_log() + "EOF\nexit 0\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    _arm_deepseek_cli(tmp_path, monkeypatch, script)
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
    assert oneshot == []
    assert result.blocker == NAMED_BLOCKER_CLI_NO_AUTHORSHIP
    assert NAMED_BLOCKER_CLI_NO_AUTHORSHIP in result.detail
    assert CLI_EMPTY_DESCRIBED_NOT_WRITTEN in result.detail
    assert CLI_EMPTY_WRITE_TARGETS_UNREAD in result.detail
    assert result.ok is True
