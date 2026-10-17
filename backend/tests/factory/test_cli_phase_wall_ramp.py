"""CD-LAUNCH-1 GATE 1: C-BRIEF CLI must ramp before the 1500s phase wall.

Measured sess_5782f2264e0e4ff4 Continue 2026-09-10: one indivisible
C-BRIEF CLI call ~1488s; WRITER ~1490s vs phase_wall_clock_s=1500.
``factory budget ramp`` was never emitted — ``_extend_wall`` is not
called on the CLI wait path. The CLI default ``wall_clock_s`` is already
7200 (#398 ceiling), so today's ``_extend_wall`` no-ops (new_wall <=
old_wall) and the 1500s phase box never lifts.

Ramp + salvage: while the CLI is alive and approaching
``phase_wall_clock_s``, call ``_extend_wall`` so the 7200s ceiling is
actually used. Do not "fix" by raising the initial phase wall.

reuse_accept is post-CLI keep-path (sess_5782f2264e0e4ff4 run3
estate_registry / storage miss). It cannot suppress this ramp. A
Continue with no ``factory budget ramp`` line either never approached
the phase box while CLI was in-flight, or ``_cli_in_flight`` was
false — that is not a reuse_accept defect.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build.authority import BuildRole
from app.factory.build.roles import ROLE_IMPLEMENTATIONS
from app.factory.build.roles_models import RoleResult
from app.factory.build.runner import BuildBudget, RoleRunner

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"

LIVE_SESS = "sess_5782f2264e0e4ff4"
PHASE_WALL_S = 1.0
CEILING_S = 7200.0


@pytest.fixture()
def blueprint():
    return load_blueprint(SMOKE)


def test_extend_wall_lifts_phase_box_when_wall_already_at_ceiling(blueprint, tmp_path, caplog):
    """CLI default wall is already 7200 — the phase box must still lift."""
    runner = RoleRunner(
        blueprint,
        tmp_path / "w",
        budget=BuildBudget(
            wall_clock_s=CEILING_S,
            phase_wall_clock_s=1500.0,
            hard_ceiling_s=CEILING_S,
        ),
    )
    runner._run_started = 0.0
    runner._deadline = 1500.0
    runner._deadline_box["at"] = 1500.0
    caplog.set_level(logging.INFO, logger="cerebrumdev.factory.runner")

    runner._extend_wall(CEILING_S)

    assert runner.budget.wall_clock_s == CEILING_S
    assert runner._deadline == pytest.approx(CEILING_S, abs=1.0)
    assert runner._deadline_box["at"] == pytest.approx(CEILING_S, abs=1.0)
    assert any("factory budget ramp" in r.getMessage() for r in caplog.records), (
        "lifting the phase-capped live deadline must log factory budget ramp "
        "even when wall_clock_s is already at the ceiling"
    )


def test_cli_outliving_phase_wall_extends_logs_and_continues(blueprint, tmp_path, caplog):
    """A live C-BRIEF CLI that outlives the role wall must ramp, not die.

    Clock jumps past ``phase_wall_clock_s`` while the dispatch NOTE is
    still in-flight. The session continues (WRITER returns ok) and the
    live deadline is the 7200s ceiling, not the 1s role wall.
    """
    assert LIVE_SESS in __doc__
    now = {"t": 0.0}

    def clock():
        return now["t"]

    continued = {"ok": False}

    def hanging_cli(ctx):
        # Time moves forward from wherever THIS WRITER call starts. A rework
        # round re-enters WRITER later in the run (TESTER or writer-gate
        # rework); absolute values rewound the clock (1.4 -> 0.95), left the
        # re-entered phase box (start + 1s) un-approached, and read
        # coder_time_left == 1.0 -- a clock no live build has, not a ramp miss.
        start = now["t"]
        ctx.note(
            "dispatching compiled brief via FACTORY_CODE_CLI (/usr/local/bin/kimi)",
            stage="dispatch",
            source="coder CLI",
            model_call=True,
            deadline_s=7230,
            done=0,
            total=1,
        )
        now["t"] = start + PHASE_WALL_S - 0.05
        # Pulse the same inspect hook the CLI wait loop uses.
        pulse = (ctx.deadline_box or {}).get("inspect")
        assert callable(pulse), "CLI path must share deadline_box['inspect']"
        pulse()
        now["t"] = start + PHASE_WALL_S + 0.4
        pulse()
        left = ctx.coder_time_left()
        assert left is not None and left > 1.0, (
            "after the phase wall the live deadline must still have room "
            f"(coder_time_left={left})"
        )
        continued["ok"] = True
        return RoleResult(ok=True, detail="cli continued after phase wall")

    roles = dict(ROLE_IMPLEMENTATIONS)
    roles[BuildRole.WRITER] = hanging_cli
    caplog.set_level(logging.INFO, logger="cerebrumdev.factory.runner")
    runner = RoleRunner(
        blueprint,
        tmp_path / "build",
        roles=roles,
        budget=BuildBudget(
            max_rework=1,
            wall_clock_s=CEILING_S,
            phase_wall_clock_s=PHASE_WALL_S,
            hard_ceiling_s=CEILING_S,
        ),
        clock=clock,
    )
    outcome = runner.run()

    assert continued["ok"] is True
    assert any("factory budget ramp" in r.getMessage() for r in caplog.records), (
        caplog.text
    )
    boxed = runner._deadline_box.get("at")
    assert boxed is not None
    assert boxed >= runner._run_started + CEILING_S - 1.0
    # Do not "fix" by raising the initial phase wall.
    assert runner.budget.phase_wall_clock_s == PHASE_WALL_S
    # Stub WRITER may fail later gates; the contract is ramp + continue.
    assert outcome.outcome is not None
