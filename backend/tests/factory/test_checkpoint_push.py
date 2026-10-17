"""A build-branch push keeps GitHub's whole answer, retries, and refuses
oversized files before pushing.

Live 2026-10-07 (9d382ae7, co-op repro, build/plt_71c2fac9c68842a3): the
WRITER checkpoint failed with ``! [remote rejected] HEAD -> build/... (failed)``
and nothing else -- the error kept only the last 300 characters of git's
stderr, which cut every ``remote:`` line that would have named the reason,
and a single rejection failed the build outright.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.factory.build import branch_attach
from app.factory.build.builds_push import (
    BuildsPushError,
    oversized_files,
    push_with_retry,
)

TOKEN = "ghs_FAKETOKENVALUE123"


def _proc(rc: int, stderr: str = "", stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["git"], returncode=rc, stdout=stdout, stderr=stderr)


def _long_remote_rejection() -> str:
    remote = "".join(f"remote: diagnostic line {i} naming the cause of the rejection\n" for i in range(12))
    return (
        remote
        + f"To https://x-access-token:{TOKEN}@github.com/o/r.git\n"
        + " ! [remote rejected] HEAD -> build/plt_x (failed)\n"
        + "error: failed to push some refs\n"
    )


def test_a_transient_rejection_is_retried_and_the_push_succeeds(tmp_path):
    answers = [_proc(1, _long_remote_rejection()), _proc(0)]
    calls, slept = [], []

    def git(args, cwd):
        calls.append(list(args))
        return answers.pop(0)

    result = push_with_retry(git, ["push", "origin", "HEAD:b"], cwd=tmp_path, token=TOKEN,
                             label="checkpoint push failed", sleep=slept.append)
    assert result.returncode == 0
    assert len(calls) == 2
    assert slept, "a retry waits before the next attempt"


def test_a_persistent_rejection_keeps_every_remote_line_and_never_the_token(tmp_path):
    def git(args, cwd):
        return _proc(1, _long_remote_rejection())

    with pytest.raises(BuildsPushError) as err:
        push_with_retry(git, ["push"], cwd=tmp_path, token=TOKEN,
                        label="checkpoint push failed", attempts=2, sleep=lambda s: None)
    msg = str(err.value)
    assert "remote: diagnostic line 0" in msg  # the head of stderr survives
    assert "remote: diagnostic line 11" in msg
    assert "(failed)" in msg
    assert TOKEN not in msg
    assert "after 2 attempt(s)" in msg


def test_an_oversized_file_fails_typed_before_any_push(tmp_path):
    (tmp_path / "app").mkdir()
    big = tmp_path / "app" / "dump.bin"
    big.write_bytes(b"\0" * 2048)
    calls = []

    def git(args, cwd):
        calls.append(args)
        return _proc(0)

    assert oversized_files(tmp_path, limit=1024) == [("app/dump.bin", 2048)]
    import app.factory.build.builds_push as bp

    original = bp.GITHUB_MAX_FILE_BYTES
    try:
        bp.GITHUB_MAX_FILE_BYTES = 1024
        with pytest.raises(BuildsPushError) as err:
            bp.push_with_retry(git, ["push"], cwd=tmp_path, token=TOKEN, label="x",
                               sleep=lambda s: None)
    finally:
        bp.GITHUB_MAX_FILE_BYTES = original
    assert "app/dump.bin" in str(err.value)
    assert calls == [], "nothing is pushed when a file would be refused"


def test_git_metadata_is_never_counted_as_an_oversized_file(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "pack").write_bytes(b"\0" * 4096)
    assert oversized_files(tmp_path, limit=1024) == []


def test_the_checkpoint_retries_a_rejected_push_and_lands(tmp_path, monkeypatch):
    workspace = tmp_path / "ws"
    (workspace / "app").mkdir(parents=True)
    (workspace / "app" / "main.py").write_text("x = 1\n", encoding="utf-8")
    pushes = []

    def fake_git(args, cwd):
        cwd = Path(cwd)
        verb = args[0]
        if verb == "clone":
            cwd.mkdir(parents=True, exist_ok=True)
            return _proc(0)
        if verb == "push":
            pushes.append(list(args))
            return _proc(1, _long_remote_rejection()) if len(pushes) == 1 else _proc(0)
        if verb == "rev-parse":
            return _proc(0, stdout="abc1234def\n")
        return _proc(0)

    monkeypatch.setattr(branch_attach, "_git", fake_git)
    monkeypatch.setattr("app.factory.build.builds_push.PUSH_BACKOFF_S", 0.0)
    env = {"CEREBRUM_BUILDS_GITHUB_TOKEN": TOKEN, "CEREBRUM_BUILDS_REPO": "o/r"}
    sha = branch_attach.checkpoint(workspace, "build/plt_x", "factory: WRITER passed", env)
    assert sha == "abc1234def"
    assert len(pushes) == 2, "the first rejection is retried, not fatal"
