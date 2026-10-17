"""The wall-clock ceiling must be a bound, not a convention.

``budget_inspect.CEILING_S`` is 7200s and ``next_stage_wall`` caps its
proposals at it. But ``RoleRunner._extend_wall`` is the only writer of the
deadline, and it accepts any float — and two of its six callers do not come
from the inspector at all:

  runner.py:675  needed = elapsed + PILOT_SUITE_TAIL_S; self._extend_wall(needed)
  runner.py:799  self._extend_wall(elapsed_now + float(PILOT_SUITE_TAIL_S))

Both grow with elapsed time and neither is compared to CEILING_S. So the
ceiling holds on the paths that ask the inspector and is bypassed on the
paths that do not — which is a ceiling nobody can rely on, and the reason
a leftover FACTORY_BUILD_WALL_CLOCK_S was ever able to matter.

Making it a field of BuildBudget and clamping inside the single writer means
no caller can exceed it, whatever it passes.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build.runner import BuildBudget, RoleRunner

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"


@pytest.fixture()
def blueprint():
    return load_blueprint(SMOKE)


def _armed(blueprint, tmp_path, *, wall, ceiling):
    """A runner with a live deadline, as the phase loop would leave it."""
    runner = RoleRunner(
        blueprint,
        tmp_path / "w",
        budget=BuildBudget(wall_clock_s=wall, hard_ceiling_s=ceiling),
    )
    runner._run_started = runner.clock()
    runner._deadline = runner._run_started + wall
    runner._deadline_box["at"] = runner._deadline
    return runner


def test_the_budget_declares_a_hard_ceiling():
    b = BuildBudget()
    assert b.hard_ceiling_s == 7200.0
    assert b.wall_clock_s <= b.hard_ceiling_s


def test_the_ceiling_matches_the_inspector_s():
    """One number, not two that can drift apart."""
    from app.factory.build.budget_inspect import CEILING_S

    assert BuildBudget().hard_ceiling_s == CEILING_S


def test_extend_wall_clamps_to_the_ceiling(blueprint, tmp_path):
    runner = _armed(blueprint, tmp_path, wall=60.0, ceiling=120.0)
    runner._extend_wall(10_000.0)
    assert runner.budget.wall_clock_s == 120.0
    assert runner._deadline == pytest.approx(runner._run_started + 120.0, abs=1.0)


def test_an_extension_below_the_ceiling_is_untouched(blueprint, tmp_path):
    runner = _armed(blueprint, tmp_path, wall=60.0, ceiling=7200.0)
    runner._extend_wall(300.0)
    assert runner.budget.wall_clock_s == 300.0


def test_extend_wall_still_never_shrinks(blueprint, tmp_path):
    runner = _armed(blueprint, tmp_path, wall=600.0, ceiling=7200.0)
    runner._extend_wall(60.0)
    assert runner.budget.wall_clock_s == 600.0


def test_repeated_extensions_cannot_walk_past_the_ceiling(blueprint, tmp_path):
    """The shape of the two uncapped callers: elapsed + tail, over and over."""
    runner = _armed(blueprint, tmp_path, wall=60.0, ceiling=200.0)
    for target in (100.0, 150.0, 199.0, 5_000.0, 9_999.0):
        runner._extend_wall(target)
    assert runner.budget.wall_clock_s == 200.0
    assert runner._deadline <= runner._run_started + 200.0


def test_the_deadline_box_is_clamped_too(blueprint, tmp_path):
    """The box is what a live phase reads; it must not outrun the deadline."""
    runner = _armed(blueprint, tmp_path, wall=60.0, ceiling=120.0)
    runner._extend_wall(10_000.0)
    assert runner._deadline_box["at"] <= runner._run_started + 120.0


def test_a_ceiling_of_zero_means_unbounded(blueprint, tmp_path):
    """0 disables the bound, matching how wall_clock_s already reads 0."""
    runner = _armed(blueprint, tmp_path, wall=60.0, ceiling=0.0)
    runner._extend_wall(10_000.0)
    assert runner.budget.wall_clock_s == 10_000.0
