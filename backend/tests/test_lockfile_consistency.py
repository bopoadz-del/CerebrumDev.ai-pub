"""Lockfile-consistency twin: mutation-tested, fail-closed, not automerge-only.

The #244 class is a requirements.txt (or package.json) bump with no lock
delta. dependabot-automerge.yml arms ``gh pr merge --auto`` and must not be
the only gate — a red CI check is what blocks the merge. These tests feed
synthetic diffs; they do not depend on a live dependabot PR.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check_lockfile_consistency.py"
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"
AUTOMERGE_YML = REPO_ROOT / ".github" / "workflows" / "dependabot-automerge.yml"


def _load_script():
    spec = importlib.util.spec_from_file_location("check_lockfile_consistency", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def checker():
    assert SCRIPT.is_file(), f"missing {SCRIPT}"
    return _load_script()


def _run(*args: str, stdin: str | None = None, cwd: Path | None = None):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        input=stdin,
        cwd=cwd or REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "backend").mkdir(parents=True)
    (repo / "frontend").mkdir(parents=True)
    (repo / "backend" / "requirements.txt").write_text("foo==1.0.0\n", encoding="utf-8")
    (repo / "backend" / "requirements.lock").write_text(
        "foo==1.0.0\nbar==2.0.0\n", encoding="utf-8"
    )
    (repo / "frontend" / "package.json").write_text('{"name":"x","version":"1.0.0"}\n', encoding="utf-8")
    (repo / "frontend" / "package-lock.json").write_text('{"lockfileVersion":3}\n', encoding="utf-8")
    _git(repo, "init")
    _git(repo, "config", "user.email", "ci@example.com")
    _git(repo, "config", "user.name", "ci")
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    return repo


# -- mutation: synthetic path sets (no live dependabot PR) -----------------


def test_a_requirements_txt_bump_without_lock_is_inconsistent(checker):
    findings = checker.inconsistencies(["backend/requirements.txt"])
    assert findings, "txt-only bump must be a finding"
    assert any("requirements.txt" in f and "requirements.lock" in f for f in findings)


def test_b_matching_lock_delta_is_consistent(checker):
    assert checker.inconsistencies(
        ["backend/requirements.txt", "backend/requirements.lock"]
    ) == []


def test_c_lock_only_is_consistent(checker):
    assert checker.inconsistencies(["backend/requirements.lock"]) == []


def test_package_json_without_lock_is_inconsistent(checker):
    findings = checker.inconsistencies(["frontend/package.json"])
    assert findings
    assert any("package.json" in f for f in findings)


def test_package_json_with_package_lock_is_consistent(checker):
    assert checker.inconsistencies(
        ["frontend/package.json", "frontend/package-lock.json"]
    ) == []


def test_package_json_with_yarn_lock_is_consistent(checker):
    assert checker.inconsistencies(
        ["frontend/package.json", "frontend/yarn.lock"]
    ) == []


def test_unrelated_paths_are_consistent(checker):
    assert checker.inconsistencies(["README.md", "backend/app/main.py"]) == []


# -- mutation: CLI exit codes on synthetic --changed diffs -----------------


def test_cli_a_txt_without_lock_exits_1():
    proc = _run("--changed", "backend/requirements.txt")
    assert proc.returncode == 1, proc.stderr
    assert "requirements.lock" in proc.stderr


def test_cli_b_matching_lock_delta_exits_0():
    proc = _run("--changed", "backend/requirements.txt", "backend/requirements.lock")
    assert proc.returncode == 0, proc.stderr
    assert "ok:" in proc.stdout


def test_cli_c_lock_only_exits_0():
    proc = _run("--changed", "backend/requirements.lock")
    assert proc.returncode == 0, proc.stderr


def test_cli_stdin_txt_without_lock_exits_1():
    proc = _run("--stdin", stdin="backend/requirements.txt\n")
    assert proc.returncode == 1, proc.stderr


# -- mutation: synthetic git diffs (the CI --base path) --------------------


def test_git_a_txt_bump_without_lock_exits_1(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "backend" / "requirements.txt").write_text("foo==1.1.0\n", encoding="utf-8")
    _git(repo, "add", "backend/requirements.txt")
    _git(repo, "commit", "-m", "txt only — the #244 class")
    proc = _run("--base", "HEAD~1", cwd=repo)
    assert proc.returncode == 1, proc.stderr
    assert "requirements.lock" in proc.stderr


def test_git_b_matching_lock_delta_exits_0(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "backend" / "requirements.txt").write_text("foo==1.1.0\n", encoding="utf-8")
    (repo / "backend" / "requirements.lock").write_text(
        "foo==1.1.0\nbar==2.0.0\n", encoding="utf-8"
    )
    _git(repo, "add", "backend/requirements.txt", "backend/requirements.lock")
    _git(repo, "commit", "-m", "txt + lock")
    proc = _run("--base", "HEAD~1", cwd=repo)
    assert proc.returncode == 0, proc.stderr


def test_git_c_lock_only_exits_0(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "backend" / "requirements.lock").write_text(
        "foo==1.0.0\nbar==2.0.1\n", encoding="utf-8"
    )
    _git(repo, "add", "backend/requirements.lock")
    _git(repo, "commit", "-m", "lock only")
    proc = _run("--base", "HEAD~1", cwd=repo)
    assert proc.returncode == 0, proc.stderr


def test_git_cannot_determine_diff_fails_closed(tmp_path):
    repo = _init_repo(tmp_path)
    proc = _run("--base", "this-ref-does-not-exist", cwd=repo)
    assert proc.returncode == 1, proc.stdout
    assert "cannot determine changed files" in proc.stderr


# -- wiring: the twin lives in CI, not only in automerge -------------------


def _ci_doc():
    return yaml.safe_load(CI_YML.read_text(encoding="utf-8"))


def _automerge_doc():
    return yaml.safe_load(AUTOMERGE_YML.read_text(encoding="utf-8"))


def test_ci_has_a_fail_closed_lockfile_job():
    """A dedicated job, not continue-on-error. Deleting it reopens #244."""
    doc = _ci_doc()
    jobs = doc.get("jobs") or {}
    assert "lockfile-consistency" in jobs, "ci.yml must have a lockfile-consistency job"
    job = jobs["lockfile-consistency"]
    assert job.get("continue-on-error") in (None, False)
    steps = job.get("steps") or []
    runs = "\n".join(str(s.get("run") or "") for s in steps)
    assert "check_lockfile_consistency.py" in runs
    assert "--base" in runs
    # A red check is the gate. Do not skip the job, and do not let a step
    # failure be ignored.
    for step in steps:
        assert step.get("continue-on-error") in (None, False)
        assert step.get("if") != "always()"


def test_backend_job_also_runs_the_checker():
    """Backend (pytest) is an existing required check. Fail it too.

    A newly named job is invisible to branch protection until someone adds
    it. Running the same script from the backend job means a #244-class
    bump goes red on a check auto-merge already waits for.
    """
    job = (_ci_doc().get("jobs") or {}).get("backend") or {}
    runs = "\n".join(str(s.get("run") or "") for s in (job.get("steps") or []))
    assert "check_lockfile_consistency.py" in runs


def test_automerge_still_queues_behind_required_checks():
    """Automerge must keep ``--auto``. Skipping CI would re-open #244."""
    checked = 0
    for job in (_automerge_doc().get("jobs") or {}).values():
        for step in job.get("steps") or []:
            for line in str(step.get("run") or "").splitlines():
                code = line.split("#", 1)[0]
                if "gh pr merge" in code:
                    checked += 1
                    assert "--auto" in code
                    assert "--admin" not in code
    assert checked, "automerge lost gh pr merge --auto"


def test_automerge_does_not_inspect_lockfiles():
    """Inspection belongs in CI. pull_request_target must not read the PR tree."""
    for job in (_automerge_doc().get("jobs") or {}).values():
        for step in job.get("steps") or []:
            run_code = "\n".join(
                line.split("#", 1)[0] for line in str(step.get("run") or "").splitlines()
            )
            assert "requirements.lock" not in run_code
            assert "package-lock.json" not in run_code
            assert "check_lockfile_consistency" not in run_code
            assert "actions/checkout" not in str(step.get("uses") or "")


# ── the dependabot exemption ───────────────────────────────────────────────


_DEPENDABOT = "dependabot[bot]"


def _lockfile_steps():
    """Every ci.yml step that runs the checker, plus its ``if`` expression."""
    found = []
    for job_id, job in (_ci_doc().get("jobs") or {}).items():
        for step in job.get("steps") or []:
            if "check_lockfile_consistency.py" in str(step.get("run") or ""):
                found.append((job_id, step))
    return found


def test_the_exemption_is_narrow_and_keyed_to_the_pr_author():
    """dependabot bumps a manifest and cannot write the lock, so every bump PR
    was red on this step alone. The exemption is the owner's call; this test
    is the fence around it -- widening that ``if`` to skip the gate for
    everyone is the failure mode, and would otherwise pass unnoticed.

    Keyed to the PR AUTHOR, not ``github.actor``: actor is whoever triggered
    the latest event, so a human clicking "re-run failed checks" on a
    dependabot PR would turn it red again.
    """
    steps = _lockfile_steps()
    assert steps, "ci.yml no longer runs check_lockfile_consistency.py at all"

    for job_id, step in steps:
        condition = str(step.get("if") or "").strip()
        assert condition, (
            f"{job_id}: the lockfile step lost its condition; it now runs for "
            "dependabot too and every bump PR goes red again"
        )
        assert condition == (
            f"github.event.pull_request.user.login != '{_DEPENDABOT}'"
        ), f"{job_id}: unexpected condition on the lockfile gate: {condition!r}"


def test_a_human_pr_is_still_gated():
    """The exemption must not have become a skip for everybody. Evaluated, not
    eyeballed: the condition is exactly one inequality on one login, so any
    author that is not dependabot still runs the checker."""
    for job_id, step in _lockfile_steps():
        condition = str(step.get("if") or "")
        login = condition.split("!=")[-1].strip().strip("'\"")
        assert login == _DEPENDABOT, f"{job_id}: exempts {login!r}, not dependabot"
        # A second exempted actor would need a second clause.
        assert "||" not in condition and "&&" not in condition, (
            f"{job_id}: the exemption grew a clause: {condition!r}"
        )


def test_both_gate_sites_carry_the_exemption():
    """The checker runs in two places -- its own job and a step inside the
    already-required "Backend (pytest)" check. Exempting one leaves the bump
    PR red on the other, which is how this was first noticed."""
    jobs = {job_id for job_id, _ in _lockfile_steps()}
    assert {"lockfile-consistency", "backend"} <= jobs, jobs


# -- content: requirements.txt pins must equal requirements.lock ----------
# The path-diff twin only asks "did both files move in this PR". Under the
# dependabot exemption a txt-only bump merges, and from then on no diff ever
# shows the drift again. These compare file contents instead.

REQ_TXT = REPO_ROOT / "backend" / "requirements.txt"
REQ_LOCK = REPO_ROOT / "backend" / "requirements.lock"


@pytest.mark.parametrize(
    "req, lock, expected",
    [
        ("sqlalchemy==2.1.4\n", "SQLAlchemy==2.0.52\n", ("sqlalchemy==2.1.4", "2.0.52")),
        ("stripe==16.0.0\n", "stripe==15.6.1\n", ("stripe==16.0.0", "15.6.1")),
        # extras pin: the [binary] group must not drop the pin out of the check
        ("psycopg[binary]==3.3.6\n", "psycopg==3.3.5\n", ("psycopg==3.3.6", "3.3.5")),
        # spacing around ==
        ("foo == 1.1\n", "foo==1.0\n", ("foo==1.1", "foo==1.0")),
        # indented line
        ("    foo==1.1\n", "foo==1.0\n", ("foo==1.1", "foo==1.0")),
        # environment marker after ;
        ('foo==1.1;python_version<"3.13"\n', "foo==1.0\n", ("foo==1.1", "foo==1.0")),
    ],
)
def test_pin_drift_flags_a_version_mismatch(checker, req, lock, expected):
    findings = checker.pin_drift(req, lock)
    assert len(findings) == 1, findings
    for part in expected:
        assert part in findings[0], findings[0]


@pytest.mark.parametrize(
    "req, lock",
    [
        ("prometheus-client==0.21.1\nnumpy<2.5\n", "prometheus_client==0.21.1\nnumpy==2.4.6\n"),
        ("psycopg[binary]==3.3.6\n", "psycopg==3.3.6\n"),
        # dotted names normalise to hyphens (PEP 503)
        ("zope.interface==7.0\n", "zope-interface==7.0\n"),
        # trailing comment with no space is not part of the version
        ("foo==1.0#pinned\n", "foo==1.0\n"),
        ('foo==1.0;python_version<"3.13"\n', "foo==1.0\n"),
    ],
)
def test_pin_drift_accepts_equal_pins(checker, req, lock):
    assert checker.pin_drift(req, lock) == []


@pytest.mark.parametrize(
    "req, lock, missing",
    [("foo==1.0.0\n", "bar==2.0.0\n", "foo"), ("baz==3.0\n", "", "baz")],
)
def test_pin_drift_flags_a_pin_missing_from_the_lock(checker, req, lock, missing):
    findings = checker.pin_drift(req, lock)
    assert len(findings) == 1 and "(missing)" in findings[0]
    assert findings[0].startswith(f"lockfile-consistency: {missing}==")


@pytest.mark.parametrize(
    "req, lock, order",
    [
        ("zeta==2\nalpha==2\n", "zeta==1\nalpha==1\n", ["alpha", "zeta"]),
        ("b==2\na==2\nc==2\n", "a==1\nb==1\nc==1\n", ["a", "b", "c"]),
    ],
)
def test_pin_drift_findings_are_sorted_by_name(checker, req, lock, order):
    names = [f.split()[1].split("==")[0] for f in checker.pin_drift(req, lock)]
    assert names == order


@pytest.mark.parametrize("bump_first_pin, expected_count", [(False, 0), (True, 1)])
def test_repo_requirements_txt_agrees_with_requirements_lock(
    checker, bump_first_pin, expected_count
):
    """The shipped pair must agree; the same pair with one pin moved must not."""
    req = REQ_TXT.read_text(encoding="utf-8")
    if bump_first_pin:
        name, version = next(iter(checker.exact_pins(req).items()))
        req = req + f"\n{name}=={version}.post999\n"
    findings = checker.pin_drift(req, REQ_LOCK.read_text(encoding="utf-8"))
    assert len(findings) == expected_count, "\n".join(findings)


@pytest.mark.parametrize(
    "req, lock",
    [
        ("foo==1.1#pinned\n", "foo==1.0\n"),
        ("bar==2.5  # keep in step with the image\n", "bar==2.4\n"),
    ],
)
def test_pin_drift_flags_a_drifting_pin_with_a_trailing_comment(checker, req, lock):
    """The comment strip keeps the pin, not the comment: drift is still seen."""
    findings = checker.pin_drift(req, lock)
    assert len(findings) == 1, findings
