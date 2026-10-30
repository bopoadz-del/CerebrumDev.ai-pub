"""CI-M-c: the mutation-gate job in ci.yml is a gate, not a report.

``job_findings`` reads the real job and names anything that would let it go
green without running or without failing: a job/step ``if: false``,
``continue-on-error``, a narrowed ``--src`` / ``--tests``, a swallowed exit
(``|| true`` and friends) or a missing ``set -e``. The checker is itself
mutation-tested below: each weakening of the real job must be caught.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path

import pytest
import yaml

CI = next(p / ".github" / "workflows" / "ci.yml" for p in Path(__file__).resolve().parents if (p / ".github" / "workflows" / "ci.yml").is_file())
JOB = "mutation-gate"
GATE_SCRIPTS = {"scripts/mutation_gate.py": ("--src", "backend/app"), "scripts/two_input_lint.py": ("--tests", "backend/tests")}
# A non-zero exit can be swallowed by ``|| x``, by ``; x`` after the command,
# by any ``exit 0`` / ``true`` statement, or by turning errexit off.
SWALLOW = re.compile(r"(\|\||;|&&)\s*(true|:|exit\s+0|echo)(?=\s|$)|^\s*(exit\s+0|true|:)\s*$|\bset\s+\+[a-z]*e", re.M)
PR_BASE = "${{ github.event.pull_request.base.sha }}"
OK_IF = {None, "always()", "${{ always() }}"}


def _workflow():
    return yaml.safe_load(CI.read_text("utf-8"))


def job_findings(job: dict) -> list:
    out = []
    if job.get("if") not in (None,):
        out.append(f"job if: {job.get('if')!r}")
    if job.get("continue-on-error"):
        out.append("job continue-on-error")
    seen = set()
    for step in job.get("steps") or []:
        run = str(step.get("run") or "")
        scripts = [s for s in GATE_SCRIPTS if s in run]
        if step.get("continue-on-error"):
            out.append(f"step continue-on-error: {step.get('name')}")
        if not scripts:
            continue
        if step.get("if") not in OK_IF:
            out.append(f"step if: {step.get('if')!r}")
        if SWALLOW.search(run):
            out.append(f"swallowed exit: {step.get('name')}")
        if not re.search(r"set -[a-z]*e", run):
            out.append(f"no set -e: {step.get('name')}")
        # The base is the PR's base sha, never HEAD or any other ref.
        bases = re.findall(r"--base[ =](\S+)", run)
        assigns = re.findall(r"^\s*(?:export\s+)?BASE=(.*)$", run, re.M)
        if not bases or any(b.strip('"') != "$BASE" for b in bases) or not assigns or any(a.strip().strip('"') != PR_BASE for a in assigns):
            out.append(f"base not the PR base sha: {step.get('name')}: {bases} {assigns}")
        for s in scripts:
            # The script must actually be executed: a line that starts with the
            # interpreter, not echoed, commented or quoted.
            if not re.search(rf"^\s*python3?\s+{re.escape(s)}\b", run, re.M):
                out.append(f"gate not executed: {s}")
                continue
            seen.add(s)
            flag, root = GATE_SCRIPTS[s]
            vals = re.findall(rf"{re.escape(flag)}[ =](\S+)", run)
            if vals != [root]:
                out.append(f"narrowed {flag}: {vals}")
    out += [f"gate not run: {s}" for s in GATE_SCRIPTS if s not in seen]
    return out


def test_workflow_runs_on_pull_requests():
    wf = _workflow()
    on = wf.get("on", wf.get(True))
    assert "pull_request" in on


def test_real_job_is_a_gate():
    findings = job_findings(_workflow()["jobs"][JOB])
    assert not findings, findings


def _step(job, script):
    return next(s for s in job["steps"] if script in str(s.get("run") or ""))


def _m_job_if(j):
    j["if"] = "false"


def _m_job_coe(j):
    j["continue-on-error"] = True


def _m_step_if(j):
    _step(j, "scripts/mutation_gate.py")["if"] = "false"


def _m_step_coe(j):
    _step(j, "scripts/two_input_lint.py")["continue-on-error"] = True


def _m_narrow_src(j):
    s = _step(j, "scripts/mutation_gate.py")
    s["run"] = s["run"].replace("--src backend/app", "--src backend/app/factory/build")


def _m_narrow_tests(j):
    s = _step(j, "scripts/two_input_lint.py")
    s["run"] = s["run"].replace("--tests backend/tests", "--tests backend/tests/none")


def _m_or_true(j):
    s = _step(j, "scripts/mutation_gate.py")
    s["run"] = s["run"].rstrip() + " || true\n"


def _m_or_exit0(j):
    s = _step(j, "scripts/two_input_lint.py")
    s["run"] = s["run"].rstrip() + " || exit 0\n"


def _m_no_set_e(j):
    s = _step(j, "scripts/mutation_gate.py")
    s["run"] = s["run"].replace("set -euo pipefail", "")


def _m_drop_gate(j):
    j["steps"] = [s for s in j["steps"] if "scripts/two_input_lint.py" not in str(s.get("run") or "")]


def _sub(script, old, new):
    def m(j):
        s = _step(j, script)
        assert old in s["run"]
        s["run"] = s["run"].replace(old, new)
    m.__name__ = f"_m_{script.split('/')[-1]}_{new.strip()[:20]}"
    return m


MG, LINT = "scripts/mutation_gate.py", "scripts/two_input_lint.py"
NEW_MUTANTS = [
    _sub(MG, '--summary "$RUNNER_TEMP/cim.md"', '--summary "$RUNNER_TEMP/cim.md"; true'),
    _sub(LINT, '--summary "$RUNNER_TEMP/cim_lint.md"', '--summary "$RUNNER_TEMP/cim_lint.md"; true'),
    _sub(MG, '--summary "$RUNNER_TEMP/cim.md"', '--summary "$RUNNER_TEMP/cim.md"\nexit 0'),
    _sub(LINT, '--summary "$RUNNER_TEMP/cim_lint.md"', '--summary "$RUNNER_TEMP/cim_lint.md"\nexit 0'),
    _sub(MG, "set -euo pipefail", "set -euo pipefail\nset +e"),
    _sub(LINT, "set -euo pipefail", "set -euo pipefail\nset +e"),
    _sub(MG, '--base "$BASE"', "--base HEAD"),
    _sub(LINT, '--base "$BASE"', "--base HEAD"),
    _sub(MG, 'BASE="${{ github.event.pull_request.base.sha }}"', "BASE=HEAD"),
    _sub(LINT, 'BASE="${{ github.event.pull_request.base.sha }}"', 'BASE="HEAD~1"'),
    _sub(MG, "python3 scripts/mutation_gate.py", "echo python3 scripts/mutation_gate.py"),
    _sub(LINT, "python3 scripts/two_input_lint.py", "echo python3 scripts/two_input_lint.py"),
]


@pytest.mark.parametrize("mutant", [
    _m_job_if, _m_job_coe, _m_step_if, _m_step_coe, _m_narrow_src, _m_narrow_tests,
    _m_or_true, _m_or_exit0, _m_no_set_e, _m_drop_gate, *NEW_MUTANTS,
], ids=lambda f: f.__name__)
def test_every_weakening_is_caught(mutant):
    job = copy.deepcopy(_workflow()["jobs"][JOB])
    mutant(job)
    assert job_findings(job), mutant.__name__


@pytest.mark.parametrize("run,swallowed", [
    ("python3 x; true", True), ("python3 x\nexit 0", True), ("set +e\npython3 x", True),
    ("python3 x && true", True), ("python3 x || :", True),
    ("python3 x\nexit 1", False), ("python3 x --flag", False), ("echo done before\npython3 x", False),
])
def test_swallow_pattern(run, swallowed):
    assert bool(SWALLOW.search(run)) is swallowed
