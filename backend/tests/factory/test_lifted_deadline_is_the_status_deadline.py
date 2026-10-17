"""The build status reads the LIFTED model-call deadline, never the dispatch one.

Live 2026-10-06 (smoke run 37418371161): "coder LLM timed out after 1803s
(deadline 1800s) -- codewhale writer CLI started -- model call in flight".
build_jobs._model_call_overdue judges the latest OPEN model-call NOTE by its
age and its deadline_s. The worker writes that NOTE once, at dispatch
(deadline_s=1800); the runner's ramp (_extend_wall) lifted the live deadline
without writing anything, so the status reader still timed the call out at
1800s.

One source of truth: the ledger's latest open model-call NOTE. When the ramp
lifts the live deadline while a coding-agent call is in flight, it appends a
NOTE for that call carrying the lifted deadline; the reader already prefers
the latest open NOTE.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from app.factory import build_jobs
from app.factory.blueprint import load_blueprint
from app.factory.build import codewhale_worker
from app.factory.build.ledger import EventKind
from app.factory.build.runner import CLI_PHASE_RAMP_HEADROOM_S, BuildBudget, RoleRunner
from app.factory.llm_watchdog import MODEL_CALL_GRACE_S

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"
DISPATCH_S = 1800.0
CEILING_S = 7200.0


def _runner(tmp_path):
    clock = {"t": 0.0}
    runner = RoleRunner(
        load_blueprint(SMOKE),
        tmp_path / "build",
        budget=BuildBudget(
            max_rework=2,
            wall_clock_s=CEILING_S,
            phase_wall_clock_s=DISPATCH_S,
            hard_ceiling_s=CEILING_S,
        ),
        clock=lambda: clock["t"],
    )
    runner.workspace.mkdir(parents=True, exist_ok=True)
    runner.ledger.start_run(product_id="p", inputs_hash="h")
    runner._run_started = 0.0
    runner._deadline = DISPATCH_S
    runner._deadline_box["at"] = DISPATCH_S
    runner._deadline_box["clock"] = lambda: clock["t"]
    return runner, clock


def _open_call(runner):
    # The exact payload the WRITER relay writes for the worker's open NOTE.
    runner.ledger.append(
        EventKind.NOTE,
        detail="codewhale writer CLI started — model call in flight",
        payload={"model_call": True, "deadline_s": DISPATCH_S,
                 "provider": "deepseek", "source": "codewhale_worker"},
    )


def _aged(event, seconds_ago):
    ts = (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()
    return SimpleNamespace(ts=ts, detail=event.detail, payload=dict(event.payload or {}))


def _notes(runner):
    return [e for e in runner.ledger.events() if e.kind is EventKind.NOTE]


def test_the_ramp_records_the_lifted_deadline_for_the_open_call(tmp_path):
    runner, clock = _runner(tmp_path)
    _open_call(runner)
    clock["t"] = DISPATCH_S - 100.0  # inside the ramp's headroom

    runner._maybe_cli_phase_ramp()

    lifted = build_jobs._open_model_call_note(_notes(runner))
    assert lifted.payload.get("cli_wall_extended") is True
    assert lifted.payload["deadline_s"] == CEILING_S - clock["t"]
    assert lifted.payload["source"] == "codewhale_worker"


def test_past_the_dispatch_deadline_with_the_ramp_lifting_is_not_timed_out(tmp_path):
    runner, clock = _runner(tmp_path)
    _open_call(runner)
    clock["t"] = DISPATCH_S - 100.0
    runner._maybe_cli_phase_ramp()

    notes = _notes(runner)
    dispatch, lifted = notes[-2], notes[-1]
    # Wall-clock 1803 s after dispatch; the lift was recorded 100 s before 1800.
    aged = [_aged(dispatch, 1803.0), _aged(lifted, 103.0)]
    open_note = build_jobs._open_model_call_note(aged)
    assert build_jobs._model_call_overdue(open_note, idle_s=5.0) is None


def test_past_the_lifted_deadline_is_timed_out_with_the_lifted_value(tmp_path):
    runner, clock = _runner(tmp_path)
    _open_call(runner)
    clock["t"] = DISPATCH_S - 100.0
    runner._maybe_cli_phase_ramp()
    lifted_s = CEILING_S - clock["t"]

    notes = _notes(runner)
    aged = [_aged(notes[-2], lifted_s + 1900.0), _aged(notes[-1], lifted_s + 10.0)]
    detail = build_jobs._model_call_overdue(
        build_jobs._open_model_call_note(aged), idle_s=5.0
    )
    assert detail is not None
    assert f"deadline {int(lifted_s)}s" in detail


def test_no_call_in_flight_records_nothing(tmp_path):
    runner, clock = _runner(tmp_path)
    clock["t"] = DISPATCH_S - 100.0
    before = len(_notes(runner))
    runner._extend_wall(CEILING_S)
    assert not any(
        (e.payload or {}).get("model_call") for e in _notes(runner)[before:]
    )


def test_the_ramp_always_records_before_the_dispatch_deadline():
    """The boundary, as an invariant: the ramp fires once the box has <=
    CLI_PHASE_RAMP_HEADROOM_S left, pulses arrive at least every heartbeat,
    and the dispatch NOTE's deadline is never earlier than box end minus
    MODEL_CALL_GRACE_S -- so a lift is always recorded before the reader may
    time the dispatch NOTE out."""
    assert codewhale_worker.HEARTBEAT_EVERY_S < CLI_PHASE_RAMP_HEADROOM_S - MODEL_CALL_GRACE_S
