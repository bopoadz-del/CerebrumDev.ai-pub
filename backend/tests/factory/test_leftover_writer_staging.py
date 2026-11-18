"""A WRITER staging tree left behind by a dead pass, seen at phase start.

``RoleRunner._run_phase`` decides what to do with it: an interrupted CodeWhale
pass that narrated its steps to ``docs/writer_progress.log`` is resumed (its
files are kept and the ledger says so); any other leftover is wiped so two
attempts never compose on disk. These tests drive ``_run_phase`` directly with
a stub WRITER that records what its workspace shows and then stops, so no
gate, no later role and no product suite runs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build.authority import BuildRole
from app.factory.build.ledger import EventKind
from app.factory.build.roles import ROLE_IMPLEMENTATIONS
from app.factory.build.runner import RoleRunner, role_staging_dir

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"


class _Stop(BaseException):
    """Ends the phase once the stub WRITER has looked; not caught by the runner."""


@pytest.fixture(autouse=True)
def _codewhale_writer(monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")
    monkeypatch.setenv("FACTORY_CODEWHALE_WRITER", "1")


def _phase_start(tmp_path: Path, leftover: dict[str, str] | None):
    """Run the WRITER phase start over ``leftover`` (relpath -> text, or None
    for no staging tree at all). Returns (what the writer saw, runner)."""
    out = tmp_path / "build"
    out.mkdir()
    staging = role_staging_dir(out, BuildRole.WRITER)
    if leftover is not None:
        for rel, text in leftover.items():
            path = staging / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
    seen: dict[str, bool] = {}

    def looking_writer(ctx):
        seen["stale_model"] = ctx.workspace.exists("app/models.py")
        raise _Stop()

    roles = dict(ROLE_IMPLEMENTATIONS)
    roles[BuildRole.WRITER] = looking_writer
    runner = RoleRunner(load_blueprint(SMOKE), out, roles=roles)
    with pytest.raises(_Stop):
        runner._run_phase(BuildRole.WRITER, [])
    return seen, runner


def _resume_notes(runner: RoleRunner) -> list:
    return [
        e
        for e in runner.ledger.events()
        if e.kind is EventKind.NOTE and (e.payload or {}).get("writer_resumed")
    ]


def test_a_leftover_without_a_progress_log_is_wiped(tmp_path):
    seen, runner = _phase_start(tmp_path, {"app/models.py": "# torn\n"})
    assert seen["stale_model"] is False, "a dead pass's file reached the next writer"
    assert not runner.state.get("writer_resumed")
    assert _resume_notes(runner) == []


def test_a_leftover_with_a_progress_log_is_resumed_not_wiped(tmp_path):
    seen, runner = _phase_start(
        tmp_path,
        {"app/models.py": "# step 3 of 17\n", "docs/writer_progress.log": "STEP 3: models\n"},
    )
    assert seen["stale_model"] is True, "the interrupted pass's files were thrown away"
    assert runner.state.get("writer_resumed") is True
    assert len(_resume_notes(runner)) == 1


def test_no_leftover_means_no_resume(tmp_path):
    seen, runner = _phase_start(tmp_path, None)
    assert seen["stale_model"] is False
    assert not runner.state.get("writer_resumed")
    assert _resume_notes(runner) == []
