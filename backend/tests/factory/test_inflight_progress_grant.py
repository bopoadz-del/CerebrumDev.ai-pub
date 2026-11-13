"""F-W2b: an in-flight, producing CLI gets its wall at the stage inspect.

Live 2026-11-13 (pilot 43338448825, b6f5402): the stage-1 inspect found the
writer CLI in flight and producing (files on disk rising, STEP lines
reported) but its decision was "wait for the CLI to finish or fail" with no
``next_wall_s``. The runner only extends the live deadline when the inspect
names a wall, so the F-W2 progress-evidenced grant was never asked for and
the writer died at its own dispatch wall with most of the phase budget
unused.

Owner ruling F-W2b: when a stage inspect finds the CLI in flight with
progress evidence, the decision carries ``next_wall_s`` = the phase-bounded
wall (max(plan wall x passes, phase wall), ceiling 7200s). In flight without
progress stays clamped (denied NOTE). No CLI: no ``next_wall_s``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build import codewhale_worker
from app.factory.build.budget_inspect import (
    CEILING_S,
    STAGE_1_S,
    STAGE_2_S,
    _progress_evidenced_grant,
    phase_bounded_wall,
)
from app.factory.build.codewhale_worker import (
    FILES_ON_DISK,
    STEPS_REPORTED,
    heartbeat_line,
)
from app.factory.build.ledger import EventKind
from app.factory.build.runner import BuildBudget, RoleRunner

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"


def _runner(tmp_path, *, wall, phase, cli_started=True):
    clock = {"t": 0.0}
    runner = RoleRunner(
        load_blueprint(SMOKE),
        tmp_path / "build",
        budget=BuildBudget(
            max_rework=1,
            wall_clock_s=wall,
            phase_wall_clock_s=phase,
            hard_ceiling_s=CEILING_S,
        ),
        clock=lambda: clock["t"],
    )
    runner.workspace.mkdir(parents=True, exist_ok=True)
    runner.ledger.start_run(product_id="p", inputs_hash="h")
    runner._run_started = 0.0
    runner._deadline = wall
    runner._deadline_box["at"] = wall
    runner._deadline_box["clock"] = lambda: clock["t"]
    if cli_started:
        runner.ledger.append(
            EventKind.NOTE,
            detail="codewhale writer CLI started — model call in flight",
            payload={"model_call": True, "deadline_s": wall,
                     "provider": "deepseek", "source": "codewhale_worker"},
        )
    return runner, clock


def _heartbeat(runner, *, files, steps):
    runner.ledger.append(
        EventKind.NOTE,
        detail=heartbeat_line(600.0, files, steps),
        payload={"source": "codewhale_worker", FILES_ON_DISK: files, STEPS_REPORTED: steps},
    )


def _wall_notes(runner):
    return [
        e for e in runner.ledger.events()
        if e.kind is EventKind.NOTE and "wall_applied_s" in (e.payload or {})
    ]


def _stage_1_inspect(runner, clock):
    clock["t"] = STAGE_1_S - codewhale_worker.HEARTBEAT_EVERY_S
    return runner._maybe_stage_inspect()


@pytest.mark.parametrize(
    "wall, phase, granted",
    [
        # Staged small plan: the inspect asks for the phase budget, not 2700s.
        (STAGE_1_S, 5400.0, 5400.0),
        # Plan wall between stage 1 and stage 2 (the pilot shape): before
        # F-W2b this was "await_cli" with no wall.
        (2400.0, 5400.0, 5400.0),
        # A phase budget above the ceiling is bounded by the ceiling.
        (STAGE_1_S, 9000.0, CEILING_S),
    ],
)
def test_a_producing_cli_in_flight_gets_the_phase_bounded_wall_at_stage_1(
    tmp_path, wall, phase, granted
):
    runner, clock = _runner(tmp_path, wall=wall, phase=phase)
    _heartbeat(runner, files=33, steps=4)
    _heartbeat(runner, files=211, steps=6)

    decided = _stage_1_inspect(runner, clock)

    assert decided["decision"] == "continue_progress_evidenced"
    assert decided["next_wall_s"] == granted
    assert decided["continue"] is True
    assert f"→ {granted:g}s" in decided["reason"]
    assert runner._deadline_box["at"] == granted
    assert float(runner.budget.wall_clock_s) == granted
    notes = _wall_notes(runner)
    assert len(notes) == 1
    assert notes[0].payload["wall_applied_s"] == granted
    assert notes[0].payload["wall_requested_s"] == granted
    assert not notes[0].payload.get("wall_denied")
    assert f"extended to {granted:g}s" in notes[0].detail


@pytest.mark.parametrize(
    "beats",
    [
        [],  # no progress record at all
        [(40, 3), (40, 3)],  # heartbeats that never move
    ],
)
def test_an_idle_cli_in_flight_gets_no_grant_beyond_the_plan_clamp(tmp_path, beats):
    runner, clock = _runner(tmp_path, wall=STAGE_1_S, phase=5400.0)
    for files, steps in beats:
        _heartbeat(runner, files=files, steps=steps)
    # Progress BEFORE the previous inspect is not progress in this stage.
    if beats:
        runner.ledger.append(
            EventKind.NOTE, detail="inspect", payload={"budget_inspect": True}
        )

    decided = _stage_1_inspect(runner, clock)

    assert decided["decision"] != "continue_progress_evidenced"
    assert decided["next_wall_s"] == STAGE_2_S
    assert runner._deadline_box["at"] == STAGE_1_S
    assert float(runner.budget.wall_clock_s) == STAGE_1_S
    notes = _wall_notes(runner)
    assert len(notes) == 1
    assert notes[0].payload["wall_denied"] is True
    assert notes[0].payload["wall_applied_s"] == STAGE_1_S


@pytest.mark.parametrize("files, steps", [(33, 0), (0, 5)])
def test_no_cli_in_flight_means_no_next_wall(tmp_path, files, steps):
    runner, clock = _runner(tmp_path, wall=STAGE_1_S, phase=5400.0, cli_started=False)
    _heartbeat(runner, files=0, steps=0)
    _heartbeat(runner, files=files, steps=steps)
    clock["t"] = STAGE_1_S  # nothing live: no lead, the inspect keeps its mark

    decided = runner._maybe_stage_inspect()

    assert not decided.get("cli_in_flight")
    assert decided["decision"] != "continue_progress_evidenced"
    assert decided["next_wall_s"] is None
    assert runner._deadline_box["at"] == STAGE_1_S
    assert _wall_notes(runner) == []


@pytest.mark.parametrize(
    "plan, passes, phase, bound",
    [
        (1800.0, 1, 5400.0, 5400.0),
        (1800.0, 4, 5400.0, 7200.0),  # plan x passes, then the ceiling
        (2400.0, 2, 0.0, 4800.0),
        (0.0, 1, 0.0, None),
        ("x", 1, 5400.0, None),
    ],
)
def test_phase_bounded_wall(plan, passes, phase, bound):
    assert phase_bounded_wall(
        plan_wall_s=plan, plan_wall_passes=passes, phase_wall_s=phase
    ) == bound


_PRODUCING = {
    "cli_in_flight": True,
    "cli_steps_since_inspect": 2,
    "cli_files_grown_since_inspect": 0,
}


@pytest.mark.parametrize(
    "snapshot, decision, current, proposed, expected",
    [
        (_PRODUCING, "await_cli", 2357.0, None, 5400.0),
        (_PRODUCING, "continue_stage_2", 1800.0, 2700.0, 5400.0),
        # Never overrides a pilot-ready close or a stalled leg.
        (_PRODUCING, "already_pilot_ready", 1800.0, None, None),
        (_PRODUCING, "coder_stalled", 1800.0, None, None),
        # Never proposes a wall at or below what is held or proposed.
        (_PRODUCING, "inspect_only_high_wall_honored", 5400.0, None, None),
        (_PRODUCING, "continue_ceiling", 2700.0, 7200.0, None),
        # Slot wait or finished CLI: not in flight.
        ({**_PRODUCING, "cli_in_flight": False, "slot_waiting": True},
         "await_cli", 1800.0, None, None),
        # In flight, nothing new since the last inspect.
        ({**_PRODUCING, "cli_steps_since_inspect": 0}, "await_cli", 1800.0, None, None),
    ],
)
def test_progress_evidenced_grant(snapshot, decision, current, proposed, expected):
    assert _progress_evidenced_grant(
        snapshot,
        decision=decision,
        current_wall_s=current,
        proposed_wall_s=proposed,
        plan_wall_s=1800.0,
        plan_wall_passes=1,
        phase_wall_s=5400.0,
    ) == expected
