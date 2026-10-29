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
SWALLOW = re.compile(r"\|\|\s*(true|:|exit\s+0|echo)\b")
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
        for s in scripts:
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


@pytest.mark.parametrize("mutant", [
    _m_job_if, _m_job_coe, _m_step_if, _m_step_coe, _m_narrow_src, _m_narrow_tests,
    _m_or_true, _m_or_exit0, _m_no_set_e, _m_drop_gate,
], ids=lambda f: f.__name__)
def test_every_weakening_is_caught(mutant):
    job = copy.deepcopy(_workflow()["jobs"][JOB])
    mutant(job)
    assert job_findings(job), mutant.__name__
