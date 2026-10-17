"""The one CI workflow that runs with write permission must stay inert.

``dependabot-automerge.yml`` triggers on ``pull_request_target``. That event
runs with the BASE repository's secrets and write token while the pull request
may come from anywhere. It is safe only because the workflow never checks out,
builds, installs, or executes anything from the pull request -- it reads
metadata and calls the API.

That is a property of the file, not of anyone's memory of it, so it is asserted
here. Adding an ``actions/checkout`` (or an install/build step) to that workflow
without an explicitly pinned ``ref`` turns a dependency bump into arbitrary code
execution against the repo's own token. These tests fail before that ships.
"""

from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

WORKFLOW = (
    Path(__file__).resolve().parents[2]
    / ".github"
    / "workflows"
    / "dependabot-automerge.yml"
)

#: Steps that would run pull-request-authored code, or fetch it to be run.
_CODE_EXECUTING_ACTIONS = ("actions/checkout", "actions/setup-", "docker/build")
_CODE_EXECUTING_SHELL = (
    "npm ci", "npm install", "npm run", "yarn ", "pnpm ",
    "pip install", "python -m pytest", "make ", "docker build",
)


def _load():
    assert WORKFLOW.is_file(), f"missing workflow: {WORKFLOW}"
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _triggers(doc) -> set:
    # PyYAML parses a bare `on:` key as the boolean True.
    raw = doc.get("on", doc.get(True))
    if isinstance(raw, str):
        return {raw}
    return set(raw or ())


def _steps(doc):
    for job in (doc.get("jobs") or {}).values():
        for step in job.get("steps") or []:
            yield step


def test_the_automerge_workflow_runs_on_pull_request_target():
    """If this ever stops being true the rest of the file is guarding nothing."""
    assert "pull_request_target" in _triggers(_load())


def test_it_never_checks_out_or_builds_pull_request_code():
    doc = _load()
    offenders = []
    for step in _steps(doc):
        uses = str(step.get("uses") or "")
        for bad in _CODE_EXECUTING_ACTIONS:
            if uses.startswith(bad) and "ref:" not in str(step.get("with") or ""):
                offenders.append(f"uses: {uses}")
        run = str(step.get("run") or "")
        for bad in _CODE_EXECUTING_SHELL:
            if bad in run:
                offenders.append(f"run: ...{bad}...")
    assert not offenders, (
        "dependabot-automerge.yml runs with a write token on pull_request_target; "
        "it must not check out or execute pull-request code: " + "; ".join(offenders)
    )


def test_it_only_arms_automerge_for_patch_and_minor():
    """A major bump is a behaviour change wearing a version number."""
    doc = _load()
    arming = [
        s for s in _steps(doc)
        if "gh pr merge" in str(s.get("run") or "")
    ]
    assert arming, "no step arms auto-merge; the workflow does nothing"
    for step in arming:
        cond = str(step.get("if") or "")
        assert "semver-patch" in cond and "semver-minor" in cond, (
            "the auto-merge step must be gated on patch/minor update types"
        )
        assert "semver-major" not in cond, (
            "majors must never arm auto-merge"
        )


def test_it_uses_githubs_queue_rather_than_merging_directly():
    """`--auto` waits for required checks. A bare `gh pr merge` would not.

    Checked per command line, with shell comments stripped first. An earlier
    version of this test asserted ``"--auto" in run`` against the whole block
    and was satisfied by the explanatory comment that happens to mention the
    flag -- so deleting ``--auto`` from the actual command still passed. A
    mutation caught it. Assert on the command, never on the prose beside it.
    """
    doc = _load()
    checked = 0
    for step in _steps(doc):
        for line in str(step.get("run") or "").splitlines():
            code = line.split("#", 1)[0]
            if "gh pr merge" in code:
                checked += 1
                assert "--auto" in code, (
                    "gh pr merge without --auto merges immediately, bypassing "
                    f"the wait for required checks: {code.strip()}"
                )
    assert checked, "no gh pr merge command found to check"


def test_it_is_restricted_to_dependabot():
    doc = _load()
    conds = [str(j.get("if") or "") for j in (doc.get("jobs") or {}).values()]
    assert any("dependabot[bot]" in c for c in conds), (
        "the job must be gated on github.actor == 'dependabot[bot]'"
    )
