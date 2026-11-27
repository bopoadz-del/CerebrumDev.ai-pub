"""The stop checkpoint, called on its own: a stop outcome pushes the work as
it stood with a FAILED / STOPPED commit; an outcome that is not a stop
pushes nothing.

Every runner test ends through ``_finish`` and so reaches this function;
this file is named to sort first among them, so a broken checkpoint is
found in seconds instead of after the whole runner suite. The remote is a
real bare git repository.
"""

from __future__ import annotations

import json

import pytest

# Imported by full module path so the CI-M mutation gate (which selects
# tests by the module's dotted token) associates this file with runner.py.
import app.factory.build.runner as runner_mod
from app.factory.blueprint import load_blueprint
from app.factory.build.authority import BuildRole
from app.factory.build.ledger import BuildLedger, EventKind
from tests.factory.test_stopped_build_on_branch import (
    BLUEPRINT,
    BRANCH,
    BRIEF,
    _git,
    _on_branch,
    _workspace,
    bare_builds_remote,
)

Outcome = runner_mod.Outcome


@pytest.fixture
def remote(tmp_path, monkeypatch):
    return bare_builds_remote(tmp_path, monkeypatch)


def _runner(out):
    """A runner as run() leaves it before its first phase: the workspace's
    recorded state (the attached branch) restored."""
    bp = load_blueprint(BLUEPRINT)
    runner = runner_mod.RoleRunner(bp, out, budget=runner_mod.BuildBudget(max_rework=0), brief=BRIEF)
    runner._restore_workspace_state()
    return runner


def _stop_notes(out):
    events = BuildLedger(out / "build_ledger.jsonl").events()
    return [e for e in events if e.kind is EventKind.NOTE and "stop_checkpoint" in (e.payload or {})]


@pytest.mark.parametrize(
    "outcome, word, phase",
    [
        (Outcome.FAILED_GATE, "FAILED", BuildRole.WRITER),
        (Outcome.STOPPED_BY_OWNER, "STOPPED", BuildRole.CLONER),
    ],
)
def test_a_stop_outcome_pushes_the_work_with_a_stop_commit(tmp_path, remote, outcome, word, phase):
    out = _workspace(tmp_path)
    (out / "app").mkdir()
    (out / "app" / "main.py").write_text(f"WORD = {word!r}\n", encoding="utf-8")

    _runner(out)._checkpoint_stop(outcome, phase)

    head = _git(remote, "rev-parse", BRANCH).strip()
    subject = _git(remote, "log", "-1", "--format=%s", BRANCH).strip()
    assert subject == f"factory: {word} at {phase.value}, not certified"
    assert _on_branch(remote, "app/main.py") == f"WORD = {word!r}\n"
    manifest = json.loads(_on_branch(remote, "MANIFEST.json"))
    assert manifest["certified"] is False
    assert manifest["stopped"]["outcome"] == outcome.value
    notes = _stop_notes(out)
    assert len(notes) == 1 and notes[0].payload["stop_checkpoint"] == head


@pytest.mark.parametrize("outcome", [Outcome.SUCCESS, Outcome.HANDOFF_TO_N3, Outcome.COLLECT_ALL_REPORT])
def test_an_outcome_that_is_not_a_stop_pushes_nothing(tmp_path, remote, outcome):
    out = _workspace(tmp_path)
    (out / "app").mkdir()
    (out / "app" / "main.py").write_text("x = 1\n", encoding="utf-8")
    before = _git(remote, "rev-parse", BRANCH).strip()

    _runner(out)._checkpoint_stop(outcome, BuildRole.WRITER)

    assert _git(remote, "rev-parse", BRANCH).strip() == before
    assert _on_branch(remote, "MANIFEST.json") == ""
    assert _stop_notes(out) == []
