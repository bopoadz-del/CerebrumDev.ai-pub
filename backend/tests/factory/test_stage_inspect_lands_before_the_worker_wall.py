"""The stage-1 inspect lands BEFORE the writer's wall when a CLI call is in flight.

Live 2026-10-07 (b305c718, co-op repro sess_e1758dfdda034e1e): the codewhale
writer started ~11:23:34, was producing work (STEP 10 at 11:50:50) and was
killed at 11:53:43 ("worker_timed_out: headless job exceeded 1800.0s"). The
only lift in the ledger -- "FACTORY_CODE_CLI still in-flight -- staged wall
extended to 2700s" -- was written at 11:53:43, the same second as the kill.

Why: on a staged run the wall is 1800s, so _maybe_cli_phase_ramp extends to
budget.wall_clock_s == the box it already has (a no-op); only the stage-1
inspect lifts 1800 -> 2700, and it ran only once elapsed >= STAGE_1_S -- the
box end itself. The worker's wall is dispatch + 1800, dispatch ~20 s after
the run started (COLLECTOR/CLONER), so the inspect had to land in a ~20 s
window while pulses arrive every heartbeat (60 s). It landed on the close
relay, after the kill.
"""

from __future__ import annotations

from pathlib import Path

from app.factory import build_jobs
from app.factory.blueprint import load_blueprint
from app.factory.build import codewhale_worker
from app.factory.build.budget_inspect import STAGE_1_S
from app.factory.build.ledger import EventKind
from app.factory.build.runner import CLI_PHASE_RAMP_HEADROOM_S, BuildBudget, RoleRunner

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"


def _staged_runner(tmp_path):
    clock = {"t": 0.0}
    runner = RoleRunner(
        load_blueprint(SMOKE),
        tmp_path / "build",
        budget=BuildBudget(
            max_rework=2,
            wall_clock_s=STAGE_1_S,       # the staged 30-minute wall
            phase_wall_clock_s=7200.0,
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
    return runner, clock


def _open_codewhale_call(runner):
    runner.ledger.append(
        EventKind.NOTE,
        detail="codewhale writer CLI started — model call in flight",
        payload={"model_call": True, "deadline_s": 1800.0,
                 "provider": "deepseek", "source": "codewhale_worker"},
    )


def test_a_heartbeat_pulse_before_the_mark_lifts_the_staged_wall(tmp_path):
    runner, clock = _staged_runner(tmp_path)
    _open_codewhale_call(runner)
    # The last heartbeat pulse before the box ends: 60 s before the mark.
    clock["t"] = STAGE_1_S - codewhale_worker.HEARTBEAT_EVERY_S

    runner._maybe_stage_inspect()

    assert runner._deadline_box["at"] > STAGE_1_S, (
        "the staged wall was still 1800s one heartbeat before the box ended"
    )
    # ...and the status reader sees the lifted deadline for the open call.
    notes = [e for e in runner.ledger.events() if e.kind is EventKind.NOTE]
    open_note = build_jobs._open_model_call_note(notes)
    assert float(open_note.payload["deadline_s"]) > codewhale_worker.HEARTBEAT_EVERY_S


def test_without_a_call_in_flight_the_inspect_still_waits_for_its_mark(tmp_path):
    runner, clock = _staged_runner(tmp_path)
    clock["t"] = STAGE_1_S - codewhale_worker.HEARTBEAT_EVERY_S
    assert runner._maybe_stage_inspect() is None
    assert runner._deadline_box["at"] == STAGE_1_S


def test_the_early_window_always_holds_a_heartbeat_pulse():
    # The inspect may run once the box has <= CLI_PHASE_RAMP_HEADROOM_S left;
    # a pulse arrives every heartbeat, so at least one lands in the window
    # with a heartbeat to spare before the mark.
    assert codewhale_worker.HEARTBEAT_EVERY_S * 2 <= CLI_PHASE_RAMP_HEADROOM_S
