"""F-W2: a progress-evidenced grant reaches the writer; the ledger prints the wall applied.

Live 2026-11-12 (pilot 43307133539, 72ebc93): a small plan (wall 1800s, phase
5400s) had a codewhale writer still producing -- 2 -> 101 files on disk, STEP 9
reported -- when the stage-1 inspect granted 1800s -> 2700s. ``_extend_wall``
clamped that grant to ``plan_wall * passes`` (1800s), the ledger still said
"staged wall extended to 2700s", and the writer died at its 1800s wall. The
same inspect line said ``written=0`` while the Floor showed 101 files: two
counters for one fact.

Owner ruling (22:14Z): a grant backed by progress since the last inspect is
bounded by the phase budget and the ceiling only; a grant with NO progress
evidence stays clamped to the per-pass plan wall, and the ledger says denied.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build import codewhale_worker
from app.factory.build.budget_inspect import (
    STAGE_1_S,
    STAGE_2_S,
    inspect_build,
    written_label,
)
from app.factory.build.codewhale_worker import (
    FILES_ON_DISK,
    STEPS_REPORTED,
    count_authored_files,
    heartbeat_line,
)
from app.factory.build.ledger import EventKind
from app.factory.build.roles_handlers import _writer_worker_live_time_left
from app.factory.build.roles_models import RoleContext
from app.factory.build.runner import BuildBudget, RoleRunner
from app.factory.llm_watchdog import MODEL_CALL_GRACE_S

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"


def _small_plan_runner(tmp_path, *, phase):
    clock = {"t": 0.0}
    runner = RoleRunner(
        load_blueprint(SMOKE),
        tmp_path / "build",
        budget=BuildBudget(
            max_rework=1,
            wall_clock_s=STAGE_1_S,  # the small plan's per-pass wall
            phase_wall_clock_s=phase,
            hard_ceiling_s=7200.0,
        ),
        clock=lambda: clock["t"],
    )
    runner.workspace.mkdir(parents=True, exist_ok=True)
    runner.ledger.start_run(product_id="p", inputs_hash="h")
    runner._run_started = 0.0
    runner._deadline = STAGE_1_S
    runner._deadline_box["at"] = STAGE_1_S
    runner._deadline_box["clock"] = lambda: clock["t"]
    runner.ledger.append(
        EventKind.NOTE,
        detail="codewhale writer CLI started — model call in flight",
        payload={"model_call": True, "deadline_s": STAGE_1_S,
                 "provider": "deepseek", "source": "codewhale_worker"},
    )
    return runner, clock


def _heartbeat(runner, *, files, steps):
    runner.ledger.append(
        EventKind.NOTE,
        detail=heartbeat_line(600.0, files, steps),
        payload={"source": "codewhale_worker", FILES_ON_DISK: files, STEPS_REPORTED: steps},
    )


def _step(runner, *, steps):
    runner.ledger.append(
        EventKind.NOTE,
        detail=f"writer: STEP {steps} [WRITER]: wrote a capability",
        payload={"source": "codewhale_worker", STEPS_REPORTED: steps},
    )


def _live_time_left(runner):
    ctx = SimpleNamespace(
        coder_time_left=lambda: RoleContext.coder_time_left(
            SimpleNamespace(deadline=None, deadline_box=runner._deadline_box)
        )
    )
    return _writer_worker_live_time_left(ctx)


def _wall_notes(runner):
    return [
        e for e in runner.ledger.events()
        if e.kind is EventKind.NOTE and "wall_applied_s" in (e.payload or {})
    ]


def _inspect_one_heartbeat_before_the_mark(runner, clock):
    clock["t"] = STAGE_1_S - codewhale_worker.HEARTBEAT_EVERY_S
    return runner._maybe_stage_inspect()


@pytest.mark.parametrize(
    "phase, progress, requested, applied",
    [
        # Files on disk rising: the inspect asks for the phase-bounded wall
        # (F-W2b) and the grant goes through, bounded by the phase.
        (5400.0, "files", 5400.0, 5400.0),
        # Only steps reported, and a phase budget below the requested wall:
        # the phase budget, not the plan wall, bounds the grant.
        (2400.0, "steps", STAGE_2_S, 2400.0),
    ],
)
def test_a_small_plan_with_progress_gets_its_grant_to_live_time_left(
    tmp_path, phase, progress, requested, applied
):
    runner, clock = _small_plan_runner(tmp_path, phase=phase)
    if progress == "files":
        _heartbeat(runner, files=33, steps=0)
        _heartbeat(runner, files=101, steps=0)
    else:
        _step(runner, steps=1)
        _step(runner, steps=2)

    decided = _inspect_one_heartbeat_before_the_mark(runner, clock)

    assert decided and decided["next_wall_s"] == requested
    assert runner._extend_wall(STAGE_2_S, progress_evidenced=True) == applied
    assert runner._deadline_box["at"] == applied
    left = _live_time_left(runner)()
    assert left == pytest.approx(applied - clock["t"] - MODEL_CALL_GRACE_S)
    assert left > STAGE_1_S - clock["t"], "the grant reached the writer's live wall"

    notes = _wall_notes(runner)
    assert len(notes) == 1
    assert notes[0].payload["wall_applied_s"] == applied
    assert not notes[0].payload.get("wall_denied")
    assert f"extended to {applied:g}s" in notes[0].detail


@pytest.mark.parametrize(
    "beats",
    [
        [],  # no progress record at all
        [(0, 0), (0, 0)],  # heartbeats that never move
    ],
)
def test_a_small_plan_without_progress_stays_clamped_and_the_ledger_says_denied(
    tmp_path, beats
):
    runner, clock = _small_plan_runner(tmp_path, phase=5400.0)
    for files, steps in beats:
        _heartbeat(runner, files=files, steps=steps)

    decided = _inspect_one_heartbeat_before_the_mark(runner, clock)

    assert decided and decided["next_wall_s"] == STAGE_2_S
    assert runner._deadline_box["at"] == STAGE_1_S
    assert float(runner.budget.wall_clock_s) == STAGE_1_S
    assert _live_time_left(runner)() == pytest.approx(
        STAGE_1_S - clock["t"] - MODEL_CALL_GRACE_S
    )
    notes = _wall_notes(runner)
    assert len(notes) == 1
    assert notes[0].payload["wall_denied"] is True
    assert notes[0].payload["wall_applied_s"] == STAGE_1_S
    assert f"denied by plan wall (applied {STAGE_1_S:g}s)" in notes[0].detail
    assert not any(
        f"extended to {STAGE_2_S:g}s" in str(e.detail or "")
        for e in runner.ledger.events()
    ), "the ledger never prints a wall that did not happen"


@pytest.mark.parametrize(
    "tree",
    [
        {"app/main.py": "x", "app/routes/a.py": "y", "README.md": "z"},
        {
            "src/a.py": "1", "src/b.py": "2", "src/c/d.py": "3", "tests/t.py": "4",
            "vendor/block/x.py": "not the agent's", ".git/HEAD": "ref",
            "writer_progress.log": "progress log",
        },
    ],
)
def test_the_inspector_written_count_equals_the_floor_file_count(tmp_path, tree):
    root = tmp_path / "tree"
    for rel, text in tree.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    floor_count = count_authored_files(root)  # what the Floor's heartbeat prints

    runner, clock = _small_plan_runner(tmp_path, phase=5400.0)
    _heartbeat(runner, files=floor_count, steps=1)

    snap = inspect_build(runner.ledger, runner.workspace, runner.state)
    assert snap["cli_files_on_disk"] == floor_count
    assert f"{floor_count} file(s) on disk" in heartbeat_line(600.0, floor_count, 1)

    decided = _inspect_one_heartbeat_before_the_mark(runner, clock)
    assert f"written={floor_count} file(s) on disk" in decided["reason"]


@pytest.mark.parametrize(
    "snapshot, label",
    [
        # No coding agent has reported a file count: the capability count alone.
        ({"agent_written": 3}, "3"),
        ({"agent_written": 0, "cli_files_on_disk": None}, "0"),
        # A reported file count leads, the capability count sits beside it.
        ({"agent_written": 0, "cli_files_on_disk": 101}, "101 file(s) on disk, caps=0"),
    ],
)
def test_written_prints_the_file_count_only_when_one_was_reported(snapshot, label):
    assert written_label(snapshot) == label


@pytest.mark.parametrize("deadline", [50_000.0, 90_000.0])
def test_the_extend_note_reads_the_runner_deadline_when_the_box_is_empty(
    tmp_path, deadline
):
    runner, clock = _small_plan_runner(tmp_path, phase=5400.0)
    runner._deadline_box.pop("at", None)
    runner._deadline = deadline
    clock["t"] = 100.0

    runner._emit_cli_watchdog_extend(
        {"next_wall_s": STAGE_2_S}, applied_wall_s=STAGE_2_S, before_wall_s=STAGE_1_S
    )

    note = _wall_notes(runner)[-1]
    assert note.payload["deadline_s"] == pytest.approx(deadline - clock["t"] - 15.0)


@pytest.mark.parametrize("t", [0.0, STAGE_1_S])
def test_no_inspect_before_the_run_has_started(tmp_path, t):
    runner, clock = _small_plan_runner(tmp_path, phase=5400.0)
    runner._run_started = None
    clock["t"] = t
    before = len(runner.ledger.events())

    assert runner._maybe_stage_inspect() is None
    assert not runner._inspects_done
    assert len(runner.ledger.events()) == before
