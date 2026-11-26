"""Owner ruling 2026-11-21: the writer gets an hour, plus 30 min per fix.

30 minutes was sized before the strict grader. A producing writer was then
cut off mid-pass: live 21 Nov, ``plt_61e1edbf10e249e4`` had 167 files on
disk when the stage-1 inspect asked for 2700s and the enforcer clamped the
grant to the 1800s plan wall, because the code cycle's phase box (1500s) is
*smaller* than the plan wall -- ``max(plan x passes, phase)`` is the plan.

The ruling: the FIRST pass is one hour, and every granted rework round -- a
WRITER-gate rejection or a TESTER rejection -- adds 30 minutes on top. That
is ``first + fix x (passes - 1)``, not ``first x passes``; the latter would
hand out a second full hour per fix.

Both the proposer (``phase_bounded_wall``, read by the inspector) and the
enforcer (``RoleRunner._extend_wall``, the only writer of the deadline) use
``writer_wall_bound``, so a grant can never be offered and then clamped
away -- the exact defect that killed the build above.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.factory import build_jobs
from app.factory.blueprint import load_blueprint
from app.factory.build.budget_inspect import (
    CEILING_S,
    FIX_ALLOWANCE_S,
    phase_bounded_wall,
    writer_wall_bound,
)
from app.factory.build.runner import BuildBudget, RoleRunner

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"
BRIEF = '{"ask": "Count the items on hand.", "documents": {"file_types": [], "uploads": false}}'

#: The code cycle's first pass, and the unit a granted fix buys.
FIRST_PASS_S = 3600.0


@pytest.mark.parametrize("fixes, extra", [(1, 1800.0), (2, 3600.0)])
def test_a_fix_is_half_the_first_pass(fixes, extra):
    """One fix must buy 30 min, not a second hour."""
    assert FIX_ALLOWANCE_S == FIRST_PASS_S / 2
    assert FIX_ALLOWANCE_S * fixes == extra
    assert writer_wall_bound(FIRST_PASS_S, 1 + fixes) - FIRST_PASS_S == extra


@pytest.mark.parametrize(
    "passes, bound",
    [
        # The first pass on its own: exactly one hour.
        (1, 3600.0),
        # One granted fix (WRITER gate or TESTER): +30 min.
        (2, 5400.0),
        # Two fixes (one per gate): +30 min each, landing on the ceiling.
        (3, 7200.0),
    ],
)
def test_the_writer_bound_is_the_first_pass_plus_one_allowance_per_fix(
    passes, bound
):
    assert writer_wall_bound(FIRST_PASS_S, passes) == bound


@pytest.mark.parametrize("passes", [1, 3])
def test_no_first_wall_means_no_bound_of_its_own(passes):
    """A runner with no wall set is bounded by the ceiling, not by zero."""
    assert writer_wall_bound(0.0, passes) == 0.0


@pytest.mark.parametrize("first", [FIRST_PASS_S, 1800.0])
def test_a_pass_count_of_zero_is_still_the_first_pass(first):
    assert writer_wall_bound(first, 0) == first
    assert writer_wall_bound(first, 0) == writer_wall_bound(first, 1)


@pytest.mark.parametrize(
    "passes, expected",
    [
        (1, 3600.0),
        (2, 5400.0),
        (3, 7200.0),
        # Past the ceiling the bound stops growing: six passes would ask for
        # 12600s, and nothing may exceed CEILING_S.
        (6, CEILING_S),
    ],
)
def test_the_inspector_proposes_the_same_bound_the_enforcer_applies(
    passes, expected
):
    """Proposer and enforcer share one formula -- no promise, then a clamp."""
    assert (
        phase_bounded_wall(
            plan_wall_s=FIRST_PASS_S,
            plan_wall_passes=passes,
            phase_wall_s=FIRST_PASS_S,
        )
        == expected
    )


@pytest.mark.parametrize("passes", [1, 3])
def test_the_pilot_cycle_is_unchanged_by_the_ruling(passes):
    """Pilot is a 1800s plan with a 5400s phase: the phase, not the plan.

    ``first + fix x (passes - 1)`` equals ``first x passes`` when the first
    pass is the fix allowance, so the pilot's 90-minute phase box still
    governs and this change leaves it exactly as it was.
    """
    pilot_plan = 1800.0
    pilot_phase = 5400.0
    assert (
        phase_bounded_wall(
            plan_wall_s=pilot_plan, plan_wall_passes=passes, phase_wall_s=pilot_phase
        )
        == pilot_phase
    )


def test_the_code_cycle_defaults_hold_the_writer_s_first_pass(monkeypatch):
    """The Floor's code build: the cycle wall is an hour, and the phase box
    is at least the first-pass floor, so it never ends the pass first.

    If the phase box were smaller than the first pass (it was 1500s), the
    plan wall could be raised and the phase box would still end the pass.
    """
    from app.factory.build.budget_inspect import writer_first_pass_wall

    monkeypatch.delenv("FACTORY_BUILD_WALL_CLOCK_S", raising=False)
    monkeypatch.delenv("FACTORY_PHASE_WALL_CLOCK_S", raising=False)
    assert build_jobs._wall_clock_s() == FIRST_PASS_S
    assert build_jobs._phase_wall_clock_s() == writer_first_pass_wall()
    assert build_jobs._phase_wall_clock_s() >= writer_first_pass_wall(FIRST_PASS_S)


def _runner(tmp_path, *, passes: int) -> RoleRunner:
    runner = RoleRunner(
        load_blueprint(SMOKE),
        tmp_path / "build",
        budget=BuildBudget(
            max_rework=1,
            wall_clock_s=FIRST_PASS_S,
            phase_wall_clock_s=FIRST_PASS_S,
            hard_ceiling_s=CEILING_S,
        ),
        clock=lambda: 0.0,
        brief=BRIEF,
    )
    runner.workspace.mkdir(parents=True, exist_ok=True)
    runner.ledger.start_run(product_id="p", inputs_hash="h")
    runner._run_started = 0.0
    runner._deadline = FIRST_PASS_S
    runner._deadline_box["at"] = FIRST_PASS_S
    runner._plan_wall_passes = passes
    return runner


@pytest.mark.parametrize("passes, granted", [(2, 5400.0), (3, 7200.0)])
def test_the_enforcer_grants_the_fix_allowance_after_a_rework(
    tmp_path, passes, granted
):
    """The live defect: a granted fix must actually move the deadline."""
    runner = _runner(tmp_path, passes=passes)
    assert runner._extend_wall(granted) == granted
    assert runner._deadline == granted
    assert runner.budget.wall_clock_s == granted


@pytest.mark.parametrize("passes, asked", [(6, 12600.0), (9, 18000.0)])
def test_the_enforcer_stops_at_the_ceiling(tmp_path, passes, asked):
    """Past the ceiling the wall stops, whatever the caller asks for."""
    runner = _runner(tmp_path, passes=passes)
    assert runner._extend_wall(asked) == CEILING_S


@pytest.mark.parametrize("asked", [2700.0, 5400.0])
def test_the_first_pass_cannot_be_stretched_by_a_progress_grant(tmp_path, asked):
    """One hour is one hour: only a granted FIX adds time."""
    runner = _runner(tmp_path, passes=1)
    # The inspector's stage-1 proposal, backed by progress evidence.
    assert runner._extend_wall(asked, progress_evidenced=True) == FIRST_PASS_S
    assert runner._deadline == FIRST_PASS_S
