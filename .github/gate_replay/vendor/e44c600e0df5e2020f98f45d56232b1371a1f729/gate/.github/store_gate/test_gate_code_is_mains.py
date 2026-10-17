"""A dispatched gate scores only with the default branch's gate code.

Live 2026-10-10 (store-gate run 38040326004): the Factory dispatched the gate
on a build branch cut before builds#41. ``workflow_dispatch`` runs the workflow
file -- and the helpers under ``.github/store_gate/`` -- from the dispatched
branch's own tree, so the branch's stale ``repo_mount.exec_path`` ran the
older harness from the mounted checkout. Every runtime check judged
``/repository`` (which carries vendor/) instead of the image (which does not),
and a false 22/22 was written.

The gate now verifies, before anything is built, that the running workflow
file and every helper are blob-for-blob the default branch's. Otherwise
nothing is scored: "no score", Factory-owed (the branch must carry the current
gate). These tests run the step's own script, extracted from store-gate.yml,
against real git repositories; the default branch is deliberately not named
``main`` -- the step reads it from the event, it names none itself.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

HERE = Path(__file__).resolve().parent
WORKFLOW = HERE.parent / "workflows" / "store-gate.yml"
VERIFY = "Verify the gate code is the default branch's"
PARSE = "Parse k/N and write result"
STATUS = "Write commit status"
BASH = shutil.which("bash")
GIT = shutil.which("git")
DEFAULT = "trunk"
BRANCH = "build/plt_0000000000000009"

pytestmark = pytest.mark.skipif(BASH is None or GIT is None, reason="bash and git are required")


def _steps() -> list:
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return doc["jobs"]["store-gate"]["steps"]


def _step(name: str) -> dict:
    for step in _steps():
        if step.get("name") == name:
            return step
    raise AssertionError(f"store-gate.yml has no step named {name!r}")


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        [GIT, "-c", "core.autocrlf=false", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd, capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, (args, proc.stderr)
    return proc.stdout.strip()


def _older_repo_mount() -> str:
    """repo_mount.py as a branch cut before builds#41 carries it: exec_path
    runs an older harness from the mounted checkout."""
    text = (HERE / "repo_mount.py").read_text(encoding="utf-8")
    start = text.index("def exec_path(")
    end = text.index("def needs_staging(")
    old = (
        "def exec_path(harness: Path, harness_rel: str, image_has_harness: bool) -> str:\n"
        '    """The path ``python3`` runs inside the container."""\n'
        "    mounted = f\"{MOUNT_POINT}/{harness_rel.lstrip('/')}\"\n"
        "    if runtime_root_env(harness):\n"
        "        return mounted\n"
        "    return harness_rel if image_has_harness else mounted\n\n\n"
    )
    return text[:start] + old + text[end:]


def _checkout(tmp_path: Path, mutate=None) -> Path:
    """A remote whose default branch carries this gate, and the runner's
    single-branch checkout of a build branch cut from it (``mutate`` changes
    the branch's gate before it is pushed)."""
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "--quiet", "--bare", f"--initial-branch={DEFAULT}", str(origin))
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "--quiet", f"--initial-branch={DEFAULT}")
    shutil.copytree(HERE, seed / ".github" / "store_gate",
                    ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    (seed / ".github" / "workflows").mkdir(parents=True)
    shutil.copy2(WORKFLOW, seed / ".github" / "workflows" / "store-gate.yml")
    (seed / "app.py").write_text("print('product')\n", encoding="utf-8")
    _git(seed, "add", "-A")
    _git(seed, "commit", "--quiet", "-m", "gate")
    _git(seed, "remote", "add", "origin", str(origin))
    _git(seed, "push", "--quiet", "origin", DEFAULT)
    _git(seed, "checkout", "--quiet", "-b", BRANCH)
    if mutate is not None:
        mutate(seed)
    _git(seed, "commit", "--quiet", "--allow-empty", "-m", "product")
    _git(seed, "push", "--quiet", "origin", BRANCH)
    run = tmp_path / "runner"
    _git(tmp_path, "clone", "--quiet", "--single-branch", "--branch", BRANCH, "--depth=1",
         origin.as_uri(), str(run))
    # A plain path for the step's own fetch (a file:// URI is spelled
    # differently by every git build; the checkout above stays shallow).
    _git(run, "remote", "set-url", "origin", origin.as_posix())
    return run


def _run_step(cwd: Path, name: str, env_extra=None) -> tuple:
    github_env = cwd.parent / "github_env"
    if not github_env.exists():
        github_env.write_text("", encoding="utf-8")
    script = cwd.parent / "step.sh"
    script.write_text(_step(name)["run"], encoding="utf-8", newline="\n")
    shims = cwd.parent / "bin"
    shims.mkdir(exist_ok=True)
    python = shims / "python3"
    python.write_text(f'#!/usr/bin/env bash\nexec "{Path(sys.executable).as_posix()}" "$@"\n',
                      encoding="utf-8", newline="\n")
    python.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{shims}{os.pathsep}{env.get('PATH', '')}"
    env["GITHUB_ENV"] = str(github_env)
    # Git Bash (Windows) rewrites "<rev>:<path>" arguments on their way to a
    # native git; the gate runs on Linux, where nothing is rewritten.
    env["MSYS_NO_PATHCONV"] = "1"
    env.update(env_extra or {})
    proc = subprocess.run([BASH, "-e", str(script)], cwd=cwd, env=env,
                          capture_output=True, text=True, timeout=120)
    recorded = dict(
        line.split("=", 1)
        for line in github_env.read_text(encoding="utf-8").splitlines()
        if "=" in line
    )
    return proc, recorded


def _verify(run: Path, default: str = DEFAULT) -> tuple:
    head = _git(run, "rev-parse", "HEAD")
    return _run_step(run, VERIFY, {"GATE_DEFAULT_BRANCH": default, "GATE_WORKFLOW_SHA": head})


def _old_repo_mount(tree: Path) -> None:
    (tree / ".github" / "store_gate" / "repo_mount.py").write_text(_older_repo_mount(), encoding="utf-8")
    _git(tree, "add", "-A")


def test_a_branch_carrying_an_older_repo_mount_cannot_certify(tmp_path):
    run = _checkout(tmp_path, _old_repo_mount)
    proc, recorded = _verify(run)

    assert proc.returncode != 0, proc.stdout  # every scoring step after it is skipped
    assert recorded.get("GATE_CODE") == "stale"
    why = (run / "gate_code_stale.txt").read_text(encoding="utf-8")
    assert "stale gate code on the branch" in why
    assert ".github/store_gate/repo_mount.py" in why, why
    assert "absent" not in why, why  # compared, not unverifiable

    # Even were a score produced, the result is "no score", never k/N.
    (run / "acceptance.out").write_text("PASS cross_tenant_404 - ok\nACCEPTANCE: 22/22\n", encoding="utf-8")
    proc, _ = _run_step(run, PARSE, {"GITHUB_SHA": "f" * 40})
    report = json.loads((run / "store_gate.json").read_text(encoding="utf-8"))
    assert report["ok"] is False
    assert report["score"].startswith("no score"), report
    assert (report["passed"], report["total"]) == (0, 0)
    assert report["via"] == "stale-gate-code"
    assert "repo_mount.py" in report["detail"]
    assert proc.returncode != 0


def test_the_no_score_carries_no_k_of_n_for_the_factory_to_read(tmp_path):
    """The Factory owns a red status with no score as its own (the harness never
    ran); a "k/N" anywhere in the description would bill the product."""
    run = _checkout(tmp_path, _old_repo_mount)
    _verify(run)
    _run_step(run, PARSE, {"GITHUB_SHA": "f" * 40})
    score = json.loads((run / "store_gate.json").read_text(encoding="utf-8"))["score"]
    description = f"acceptance.py in Docker {score}"
    assert not re.search(r"\d+\s*/\s*\d+", description), description
    assert "stale gate code" in description


def test_a_branch_carrying_an_older_workflow_file_cannot_certify(tmp_path):
    def older_workflow(tree):
        path = tree / ".github" / "workflows" / "store-gate.yml"
        path.write_text(path.read_text(encoding="utf-8") + "# older\n", encoding="utf-8")
        _git(tree, "add", "-A")

    run = _checkout(tmp_path, older_workflow)
    proc, recorded = _verify(run)
    assert proc.returncode != 0
    assert recorded.get("GATE_CODE") == "stale"
    why = (run / "gate_code_stale.txt").read_text(encoding="utf-8")
    assert ".github/workflows/store-gate.yml" in why and "absent" not in why, why
    assert ".github/store_gate is" not in why, why  # the helpers are main's


def test_a_branch_with_an_extra_helper_cannot_certify(tmp_path):
    def extra(tree):
        (tree / ".github" / "store_gate" / "local_override.py").write_text("X = 1\n", encoding="utf-8")
        _git(tree, "add", "-A")

    run = _checkout(tmp_path, extra)
    proc, recorded = _verify(run)
    assert proc.returncode != 0
    assert recorded.get("GATE_CODE") == "stale"


def test_a_branch_carrying_the_default_branch_s_gate_is_scored(tmp_path):
    run = _checkout(tmp_path)
    proc, recorded = _verify(run)

    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert recorded.get("GATE_CODE") == "current"
    assert not (run / "gate_code_stale.txt").exists()
    (run / "acceptance.out").write_text("ACCEPTANCE: 22/22\n", encoding="utf-8")
    proc, _ = _run_step(run, PARSE, {"GITHUB_SHA": "f" * 40})
    report = json.loads((run / "store_gate.json").read_text(encoding="utf-8"))
    assert proc.returncode == 0 and report["ok"] is True and report["score"] == "22/22"


def test_an_unverifiable_comparison_is_no_score(tmp_path):
    run = _checkout(tmp_path)
    proc, recorded = _verify(run, default="no-such-branch")
    assert proc.returncode != 0
    assert recorded.get("GATE_CODE") == "stale"
    proc, recorded = _verify(run, default="")
    assert proc.returncode != 0


def test_the_check_runs_first_on_every_dispatch_and_never_on_a_replay():
    steps = _steps()
    names = [s.get("name") for s in steps]
    verify = names.index(VERIFY)
    # Right after the product checkout: before the replay overlay, the
    # image build and every scoring step.
    assert verify == 1 and "actions/checkout" in str(steps[0].get("uses"))
    step = steps[verify]
    assert "replay_label" in str(step.get("if"))
    assert step["env"]["GATE_DEFAULT_BRANCH"] == "${{ github.event.repository.default_branch }}"
    assert step["env"]["GATE_WORKFLOW_SHA"] == "${{ github.workflow_sha }}"
    # The step fails closed: only always() steps run after it, and those are
    # the ones that record and report the "no score".
    later_always = {s.get("name") for s in steps[verify + 1:] if "always()" in str(s.get("if") or "")}
    assert later_always == {PARSE, "Upload k/N artifact", STATUS}
    # No branch, session or sha is named by the step.
    assert not re.search(r"plt_|sess_|\b[0-9a-f]{40}\b|\bmain\b", step["run"])
