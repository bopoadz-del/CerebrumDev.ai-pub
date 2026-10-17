"""Every Store-gate dispatch runs ``main``'s gate, never the branch's older copy.

Live 2026-10-10 (cerebrum-builds store-gate run 38040326004): ops
``gate-dispatch`` POSTed ``store-gate.yml/dispatches`` with ``ref`` = the build
branch. GitHub runs that workflow -- and the helpers under
``.github/store_gate/`` -- from the dispatched branch's OWN tree; the branch was
cut before builds#41, its stale ``repo_mount.exec_path`` ran the older harness
from the mounted checkout, every runtime check judged the checkout (vendor/
present) instead of the image (no vendor/), and a false 22/22 was written.

Both dispatch paths -- ops ``gate-dispatch`` and the build's own N3 handoff
(``n3_store_gate.dispatch_store_gate``) -- now carry ``main``'s WHOLE gate onto
the branch first, with the checkpoint's own carry, and dispatch only then. A
carry that cannot be made dispatches nothing.
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

from app.factory.build import branch_attach
from app.factory.build.builds_push import STORE_GATE_PATH

REPO_ROOT = Path(__file__).resolve().parents[3]
OPS_PATH = REPO_ROOT / "scripts" / "ops.py"

HELPER = ".github/store_gate/repo_mount.py"
RETIRED = ".github/store_gate/retired_helper.py"
BRANCH = "build/plt_0000000000000007"
ENV = {"CEREBRUM_BUILDS_GITHUB_TOKEN": "t", "CEREBRUM_BUILDS_REPO": "o/r"}


def _git(cwd: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-c", "core.autocrlf=false", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=str(cwd), capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8", newline="\n")


def _commit_all(cwd: Path, msg: str) -> None:
    _git(cwd, "add", "-A")
    _git(cwd, "commit", "-q", "-m", msg)


@pytest.fixture
def remote(tmp_path, monkeypatch):
    """A bare cerebrum-builds: a build branch cut under gate v1, main at v2."""
    bare = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(bare))
    work = tmp_path / "seed"
    _git(tmp_path, "clone", "-q", str(bare), str(work))
    _git(work, "checkout", "-q", "-b", "main")
    _write(work, STORE_GATE_PATH, "gate: v1\n")
    _write(work, HELPER, "exec_path: runs an older harness from the mounted checkout\n")
    _write(work, RETIRED, "retired in v2\n")
    _commit_all(work, "gate v1")
    _git(work, "push", "-q", "origin", "main")
    _git(work, "checkout", "-q", "-b", BRANCH)
    _write(work, "app/main.py", "x = 1\n")
    _commit_all(work, "factory: CHECKPOINT")
    _git(work, "push", "-q", "origin", BRANCH)
    _git(work, "checkout", "-q", "main")
    _write(work, STORE_GATE_PATH, "gate: v2\n")
    _write(work, HELPER, "exec_path: stages an older harness inside the image\n")
    (work / RETIRED).unlink()
    _commit_all(work, "gate v2 (the fix)")
    _git(work, "push", "-q", "origin", "main")
    monkeypatch.setattr(branch_attach, "_remote_url", lambda env: str(bare))
    return bare


def _on_branch(bare: Path, rel: str) -> str:
    out = subprocess.run(["git", "show", f"{BRANCH}:{rel}"], cwd=str(bare), capture_output=True, text=True)
    return out.stdout if out.returncode == 0 else ""


def _head(bare: Path, ref: str = BRANCH) -> str:
    return _git(bare, "rev-parse", ref)


def _snapshot(bare: Path) -> dict:
    return {
        "workflow": _on_branch(bare, STORE_GATE_PATH),
        "helper": _on_branch(bare, HELPER),
        "retired": _on_branch(bare, RETIRED),
        "app": _on_branch(bare, "app/main.py"),
        "head": _head(bare),
    }


def _ops():
    spec = importlib.util.spec_from_file_location("ops_carry_under_test", OPS_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeGitHub:
    """The GitHub API as ops.gh_req sees it: the dispatch snapshots what the
    branch carries at that moment; the run is complete at the first poll."""

    def __init__(self, bare: Path):
        self.bare = bare
        self.dispatched = []

    def __call__(self, method, path, token, body=None, raw=False):
        if method == "POST" and path.endswith("/dispatches"):
            self.dispatched.append({"ref": (body or {}).get("ref"), **_snapshot(self.bare)})
            return 204, {}
        if "/actions/workflows/store-gate.yml/runs" in path:
            run = {"id": 1, "status": "completed", "conclusion": "success",
                   "created_at": "2999-01-01T00:00:00Z", "html_url": "https://example.invalid/run/1"}
            return 200, {"workflow_runs": [run]}
        if path.startswith("/repos/") and "/commits/" in path and path.endswith(BRANCH):
            return 200, {"sha": _head(self.bare)}
        if path.endswith("/statuses?per_page=100"):
            return 200, [{"context": "store-gate", "state": "success", "description": "acceptance.py in Docker 22/22"}]
        return 200, {"artifacts": []}


def _assert_mains_gate(seen: dict) -> None:
    assert seen["workflow"] == "gate: v2\n", "the dispatch ran the branch's old workflow"
    assert seen["helper"] == "exec_path: stages an older harness inside the image\n", (
        "the dispatch ran the branch's stale helper (the false 22/22)"
    )
    assert seen["retired"] == "", "a helper main retired still rides the branch"
    assert seen["app"] == "x = 1\n", "the carry touched the product"


def test_ops_gate_dispatch_carries_mains_whole_gate_before_it_dispatches(remote, monkeypatch):
    ops = _ops()
    github = FakeGitHub(remote)
    monkeypatch.setattr(ops, "gh_req", github)

    verdict = ops.dispatch_gate("o/r", BRANCH, "t", wait_s=5, poll_s=0)

    assert len(github.dispatched) == 1
    seen = github.dispatched[0]
    assert seen["ref"] == BRANCH  # the run stays on the branch: its status, its sha
    _assert_mains_gate(seen)
    assert verdict["sha"] == seen["head"] == verdict.get("gate_carried_to")


def test_ops_gate_dispatch_makes_no_commit_when_the_branch_carries_mains_gate(remote, monkeypatch):
    ops = _ops()
    monkeypatch.setattr(ops, "gh_req", FakeGitHub(remote))
    ops.dispatch_gate("o/r", BRANCH, "t", wait_s=5, poll_s=0)
    head = _head(remote)

    github = FakeGitHub(remote)
    monkeypatch.setattr(ops, "gh_req", github)
    ops.dispatch_gate("o/r", BRANCH, "t", wait_s=5, poll_s=0)

    assert github.dispatched[0]["head"] == head == _head(remote)


def test_ops_gate_dispatch_fails_closed_when_main_s_gate_cannot_be_carried(remote, tmp_path, monkeypatch):
    ops = _ops()
    github = FakeGitHub(remote)
    monkeypatch.setattr(ops, "gh_req", github)
    monkeypatch.setattr(branch_attach, "_remote_url", lambda env: str(tmp_path / "nowhere.git"))

    with pytest.raises(ops.OpsError) as err:
        ops.dispatch_gate("o/r", BRANCH, "t", wait_s=5, poll_s=0)

    assert "carry main's Store gate" in str(err.value)
    assert github.dispatched == [], "a gate that is not main's was dispatched"


class FakeOpener:
    """urlopen for github_request: a dispatch snapshots the branch."""

    def __init__(self, bare: Path):
        self.bare = bare
        self.dispatched = []

    def __call__(self, req, timeout=None):
        import json

        self.dispatched.append({"ref": json.loads(req.data or b"{}").get("ref"), **_snapshot(self.bare)})

        class _Resp:
            status = 204

            def read(self):
                return b""

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        return _Resp()


def test_the_n3_handoff_dispatch_carries_mains_whole_gate_first(remote):
    from app.factory.build.n3_store_gate import dispatch_store_gate

    opener = FakeOpener(remote)
    gated = dispatch_store_gate(BRANCH, env=ENV, opener=opener)

    assert len(opener.dispatched) == 1
    seen = opener.dispatched[0]
    assert seen["ref"] == BRANCH
    _assert_mains_gate(seen)
    # The runner records THIS sha for N3: the commit the gate judges.
    assert gated == seen["head"] == _head(remote)


def test_the_n3_handoff_dispatches_nothing_when_main_cannot_be_fetched(remote, tmp_path):
    from app.factory.build.n3_store_gate import dispatch_store_gate

    # A remote that has the branch but no main to carry from.
    _git(remote, "update-ref", "-d", "refs/heads/main")
    opener = FakeOpener(remote)
    with pytest.raises(Exception) as err:
        dispatch_store_gate(BRANCH, env=ENV, opener=opener)
    assert "main" in str(err.value)
    assert opener.dispatched == []


def test_the_handoff_records_the_sha_the_dispatch_gated(monkeypatch, tmp_path):
    """The carry may add a commit after the handoff push; N3 must read the
    gate run on the commit the gate judged, not the pushed one."""
    from app.factory.blueprint import load_blueprint
    import app.factory.build.runner as runner_mod
    from app.factory.build.authority import BuildRole
    from app.factory.build.gates import GateResult
    from app.factory.build.ledger import BuildLedger, EventKind
    from app.factory.build.roles import ROLE_IMPLEMENTATIONS, RoleResult
    from app.factory.build.runner import Outcome, RoleRunner

    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")
    monkeypatch.setenv("CEREBRUM_BUILDS_GITHUB_TOKEN", "tok")
    carried = "c" * 40

    def fake_push(workspace, *, env, session_id, run_git=None, suffix=None):
        return type("R", (), {"branch": "build/x", "sha": "abc"})()

    monkeypatch.setattr("app.factory.build.builds_push.push_workspace", fake_push)
    monkeypatch.setattr(
        "app.factory.build.n3_store_gate.dispatch_store_gate",
        lambda branch, *, env=None, opener=None, carry=None: carried,
    )

    def fake_gate(role):
        if role is BuildRole.STORE_MANAGER:
            return lambda _ctx: GateResult(
                ok=False, gate="store_manager_contract", reason="docker_unavailable",
                detail="STORE (acceptance): docker is not available",
            )
        return lambda _ctx: GateResult(ok=True, gate=f"{role.value}_stub", detail="ok")

    monkeypatch.setattr(runner_mod, "gate_for", fake_gate)
    roles = dict(ROLE_IMPLEMENTATIONS)
    roles[BuildRole.WRITER] = lambda _ctx: RoleResult(ok=True, detail="writer ran")
    out = tmp_path / "build"
    bp = load_blueprint(REPO_ROOT / "blueprints/examples/runner_smoke.yaml")
    outcome = RoleRunner(bp, out, roles=roles).run()

    assert outcome.outcome is Outcome.HANDOFF_TO_N3, outcome
    handoffs = [
        e.payload for e in BuildLedger(out / "build_ledger.jsonl").events()
        if e.kind is EventKind.NOTE and (e.payload or {}).get("builds_branch") == "build/x"
    ]
    assert handoffs and handoffs[-1].get("builds_sha") == carried
