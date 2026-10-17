"""A checkpoint carries ``main``'s WHOLE Store gate -- workflow and helpers --
never the workflow alone.

Live 2026-10-08 (cycle 2, co-op anchor, build/plt_5b8166c1a43c4622, Store-gate
run 37764042614): the branch was cut while ``main``'s gate was builds#38; the
rework checkpoint copied ``main``'s NEW ``store-gate.yml`` (builds#40, which
calls ``repo_mount.py exec-path``) onto it but left the branch's OLD
``.github/store_gate/repo_mount.py`` (no ``exec-path``). The old helper read
"exec-path" as a harness path, printed nothing usable, and the gate ran
``python3 ""`` -> "can't find '__main__' module in '/app'" -> "store-gate did
not run". One gate is the workflow plus the scripts it executes; a branch runs
whichever copy its own tree holds, so both must come from the same ``main``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.factory.build import branch_attach
from app.factory.build import builds_push
from app.factory.build.builds_push import STORE_GATE_PATH, _sync_workspace_onto_tree

#: Read by attribute so the behaviour tests run (and fail) on a tree that
#: predates the declaration.
STORE_GATE_PATHS = getattr(builds_push, "STORE_GATE_PATHS", (STORE_GATE_PATH,))

HELPER = ".github/store_gate/repo_mount.py"
STALE_HELPER = ".github/store_gate/retired_helper.py"


def _git(cwd: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-c", "core.autocrlf=false", *args], cwd=str(cwd),
        capture_output=True, text=True, check=True,
    )
    return out.stdout


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8", newline="\n")


def _commit_all(cwd: Path, msg: str) -> None:
    _git(cwd, "add", "-A")
    _git(cwd, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", msg)


@pytest.fixture
def remote(tmp_path):
    """A bare cerebrum-builds with an old-gate build branch and a newer main."""
    bare = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(bare))
    work = tmp_path / "seed"
    _git(tmp_path, "clone", "-q", str(bare), str(work))
    _git(work, "checkout", "-q", "-b", "main")
    _write(work, STORE_GATE_PATH, "gate: v1\n")
    _write(work, HELPER, "helper: v1\n")
    _write(work, STALE_HELPER, "retired in v2\n")
    _commit_all(work, "gate v1")
    _git(work, "push", "-q", "origin", "main")
    # The build branch is cut while main's gate is v1.
    _git(work, "checkout", "-q", "-b", "build/plt_test")
    _write(work, "app/main.py", "x = 1\n")
    _commit_all(work, "factory: WRITER passed")
    _git(work, "push", "-q", "origin", "build/plt_test")
    # main's gate moves to v2 while the build runs: workflow AND helpers.
    _git(work, "checkout", "-q", "main")
    _write(work, STORE_GATE_PATH, "gate: v2 calls repo_mount.py exec-path\n")
    _write(work, HELPER, "helper: v2 understands exec-path\n")
    (work / STALE_HELPER).unlink()
    _commit_all(work, "gate v2")
    _git(work, "push", "-q", "origin", "main")
    return bare


def _branch_file(bare: Path, rel: str) -> str:
    out = subprocess.run(
        ["git", "show", f"build/plt_test:{rel}"], cwd=str(bare),
        capture_output=True, text=True,
    )
    return out.stdout if out.returncode == 0 else ""


def test_a_rework_checkpoint_carries_mains_helpers_with_mains_workflow(tmp_path, remote, monkeypatch):
    workspace = tmp_path / "ws"
    _write(workspace, "app/main.py", "x = 2\n")
    monkeypatch.setattr(branch_attach, "_remote_url", lambda env: str(remote))
    env = {"CEREBRUM_BUILDS_GITHUB_TOKEN": "t", "CEREBRUM_BUILDS_REPO": "o/r"}

    branch_attach.checkpoint(workspace, "build/plt_test", "factory: REWORK", env)

    assert _branch_file(remote, STORE_GATE_PATH).startswith("gate: v2")
    assert _branch_file(remote, HELPER) == "helper: v2 understands exec-path\n", (
        "the branch runs main's new workflow with its own old helper"
    )
    assert _branch_file(remote, STALE_HELPER) == "", "a helper main retired still rides the branch"
    assert _branch_file(remote, "app/main.py") == "x = 2\n"


def test_a_workspace_copy_of_the_gate_helpers_never_replaces_mains(tmp_path):
    src, dest = tmp_path / "ws", tmp_path / "branch"
    _write(dest, STORE_GATE_PATH, "gate: main\n")
    _write(dest, HELPER, "helper: main\n")
    _write(src, HELPER, "helper: a stale copy the workspace carried\n")
    _write(src, ".github/workflows/ci.yml", "product ci\n")

    _sync_workspace_onto_tree(src, dest)

    assert (dest / HELPER).read_text(encoding="utf-8") == "helper: main\n"
    assert (dest / ".github/workflows/ci.yml").read_text(encoding="utf-8") == "product ci\n"


def test_the_gate_is_declared_once_and_includes_the_workflow():
    declared = builds_push.STORE_GATE_PATHS
    assert STORE_GATE_PATH in declared
    assert any(p != STORE_GATE_PATH for p in declared), "the gate's helpers are part of the gate"
    assert branch_attach.STORE_GATE_PATHS is declared
