"""The hardwiring gate blocks: a non-zero scan stops the commit, locally and in CI.

Owner finding 2026-10-08: the gate reported a new form and the commit was
pushed anyway, because the scan ran inside a chained command whose exit code
was not the scanner's. Locally the versioned pre-commit hook runs the scanner
directly; in CI the step runs it directly with no continue-on-error.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
HOOK = REPO / ".githooks" / "pre-commit"
CI = REPO / ".github" / "workflows" / "ci.yml"

posix_only = pytest.mark.skipif(os.name != "posix" or not shutil.which("git"), reason="POSIX shell hook")


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env=env)


def _repo_with_scanner(tmp_path: Path, exit_code: int) -> Path:
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / ".githooks").mkdir()
    shutil.copy2(HOOK, repo / ".githooks" / "pre-commit")
    (repo / "scripts" / "scan_hardwiring.py").write_text(
        f"import sys\nprint('hardwiring gate: stand-in')\nsys.exit({exit_code})\n", encoding="utf-8"
    )
    assert _git(repo, "init", "-q").returncode == 0
    assert _git(repo, "config", "core.hooksPath", ".githooks").returncode == 0
    (repo / "f.txt").write_text("x\n", encoding="utf-8")
    assert _git(repo, "add", "-A").returncode == 0
    return repo


def test_the_hook_is_versioned_and_executable():
    assert HOOK.is_file()
    assert HOOK.stat().st_mode & stat.S_IXUSR
    text = HOOK.read_text(encoding="utf-8")
    assert "scripts/scan_hardwiring.py" in text
    assert "|" not in "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))


@posix_only
def test_a_failing_scan_stops_the_commit(tmp_path):
    repo = _repo_with_scanner(tmp_path, 1)
    result = _git(repo, "commit", "-q", "-m", "would add a form")
    assert result.returncode != 0
    assert _git(repo, "rev-parse", "--verify", "HEAD").returncode != 0  # nothing committed


@posix_only
def test_a_clean_scan_lets_the_commit_through(tmp_path):
    repo = _repo_with_scanner(tmp_path, 0)
    assert _git(repo, "commit", "-q", "-m", "clean").returncode == 0


def test_the_ci_step_runs_the_scan_directly_and_blocks():
    lines = CI.read_text(encoding="utf-8").splitlines()
    start = next(i for i, l in enumerate(lines) if "Hardwiring gate" in l and "name:" in l)
    step = []
    for line in lines[start + 1:]:
        if line.strip().startswith("- name:"):
            break
        step.append(line)
    body = "\n".join(step)
    assert "scripts/scan_hardwiring.py" in body
    assert "continue-on-error" not in body
    run = next(l for l in step if "scan_hardwiring.py" in l)
    assert "|" not in run and "||" not in run
