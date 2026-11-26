"""The writer's first pass has one floor, and the ceiling is computed from it.

Before this rule the Floor gave a ``new_platform`` plan (mostly new blocks,
more to write) 2400s while a ``small`` plan got 3600s: the bigger job had
the shorter wall. One function, ``writer_first_pass_wall``, now gives every
first pass the larger of its own walls and ``WRITER_FIRST_PASS_MIN_S``; the
runner and the Floor's build path both call it, the phase box is at least
that, and the hard ceiling is the floor plus a fixed room.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build import budget_inspect
from app.factory.build.budget_inspect import (
    CEILING_ROOM_S,
    CEILING_S,
    NEW_PLATFORM_WALL_S,
    SMALL_PLATFORM_WALL_S,
    WRITER_FIRST_PASS_MIN_S,
    plan_class_wall,
    writer_first_pass_wall,
)
from app.factory.build.runner import BuildBudget, RoleRunner

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"
BRIEF = '{"ask": "Count the items on hand.", "documents": {"file_types": [], "uploads": false}}'


def test_the_floor_is_two_hours_and_the_ceiling_is_floor_plus_room():
    assert WRITER_FIRST_PASS_MIN_S == 7200.0
    assert CEILING_ROOM_S == 3600.0
    assert CEILING_S == WRITER_FIRST_PASS_MIN_S + CEILING_ROOM_S == 10800.0
    assert CEILING_S > WRITER_FIRST_PASS_MIN_S


@pytest.mark.parametrize(
    "walls, expected",
    [
        ((SMALL_PLATFORM_WALL_S,), WRITER_FIRST_PASS_MIN_S),
        ((NEW_PLATFORM_WALL_S,), WRITER_FIRST_PASS_MIN_S),
        ((3600.0, NEW_PLATFORM_WALL_S), WRITER_FIRST_PASS_MIN_S),
        ((), WRITER_FIRST_PASS_MIN_S),
        ((None, "not a number", 0.0, -5.0), WRITER_FIRST_PASS_MIN_S),
    ],
)
def test_a_wall_below_the_floor_is_lifted_to_it(walls, expected):
    assert writer_first_pass_wall(*walls) == expected


@pytest.mark.parametrize(
    "walls, expected",
    [
        ((9000.0,), 9000.0),
        ((SMALL_PLATFORM_WALL_S, 8000.0), 8000.0),
        ((7200.5, None), 7200.5),
    ],
)
def test_a_wall_above_the_floor_is_kept_not_cut(walls, expected):
    """Must not fire: the floor only raises, it never lowers a bigger wall."""
    assert writer_first_pass_wall(*walls) == expected


@pytest.mark.parametrize(
    "budget_class, expected",
    [
        ("new_platform", NEW_PLATFORM_WALL_S),
        ("small", SMALL_PLATFORM_WALL_S),
        (None, SMALL_PLATFORM_WALL_S),
        ("", SMALL_PLATFORM_WALL_S),
    ],
)
def test_plan_class_wall_reads_the_class(budget_class, expected):
    assert plan_class_wall(budget_class) == expected


def test_no_class_gets_a_shorter_first_pass_than_another():
    small = writer_first_pass_wall(plan_class_wall("small"))
    new = writer_first_pass_wall(plan_class_wall("new_platform"))
    assert new >= small >= WRITER_FIRST_PASS_MIN_S


def test_a_default_budget_holds_the_first_pass_and_the_one_ceiling():
    b = BuildBudget()
    assert b.phase_wall_clock_s == WRITER_FIRST_PASS_MIN_S
    assert b.hard_ceiling_s == CEILING_S
    explicit = BuildBudget(phase_wall_clock_s=1500.0, hard_ceiling_s=0.0)
    assert explicit.phase_wall_clock_s == 1500.0
    assert explicit.hard_ceiling_s == 0.0


@pytest.mark.parametrize("class_wall, expected", [(None, 7200.0), (9000.0, 9000.0)])
def test_a_runner_with_no_budget_gets_the_floored_class_wall(
    monkeypatch, tmp_path, class_wall, expected
):
    if class_wall is not None:
        monkeypatch.setattr(budget_inspect, "SMALL_PLATFORM_WALL_S", class_wall)
        monkeypatch.setattr(budget_inspect, "NEW_PLATFORM_WALL_S", class_wall)
    runner = RoleRunner(load_blueprint(SMOKE), tmp_path / "w", brief=BRIEF)
    assert runner.budget.wall_clock_s == expected
    assert runner.budget.hard_ceiling_s == CEILING_S


def _floor_run(monkeypatch, tmp_path, *, auto_pilot: str):
    captured = {}

    class _FakeRunner:
        def __init__(self, *a, **k):
            captured["budget"] = k.get("budget")

        def run(self):
            from app.factory.build.runner import BuildOutcome, Outcome

            return BuildOutcome(outcome=Outcome.SUCCESS)

    monkeypatch.setenv("FACTORY_AUTO_PILOT", auto_pilot)
    monkeypatch.setattr("app.factory.build.runner.RoleRunner", _FakeRunner)
    from app.factory.build_jobs import _run

    out = tmp_path / "out"
    out.mkdir()
    _run(load_blueprint(SMOKE), out, None, "code")
    return captured["budget"]


@pytest.mark.parametrize(
    "auto_pilot, class_wall, expected",
    [("0", None, 7200.0), ("1", None, 7200.0), ("0", 9000.0, 9000.0)],
)
def test_the_floor_build_path_uses_the_same_floor(
    monkeypatch, tmp_path, auto_pilot, class_wall, expected
):
    monkeypatch.delenv("FACTORY_BUILD_WALL_CLOCK_S", raising=False)
    monkeypatch.delenv("FACTORY_PHASE_WALL_CLOCK_S", raising=False)
    if class_wall is not None:
        monkeypatch.setattr(budget_inspect, "SMALL_PLATFORM_WALL_S", class_wall)
        monkeypatch.setattr(budget_inspect, "NEW_PLATFORM_WALL_S", class_wall)
    budget = _floor_run(monkeypatch, tmp_path, auto_pilot=auto_pilot)
    assert budget.wall_clock_s == expected
    assert budget.phase_wall_clock_s >= WRITER_FIRST_PASS_MIN_S
    assert budget.wall_clock_s < budget.hard_ceiling_s


@pytest.mark.parametrize("pinned", ["1500", "5000"])
def test_an_operator_pinned_wall_is_honoured_unchanged(monkeypatch, tmp_path, pinned):
    """Must not fire: an operator wall (env) is not lifted to the floor."""
    monkeypatch.setenv("FACTORY_BUILD_WALL_CLOCK_S", pinned)
    monkeypatch.setenv("FACTORY_PHASE_WALL_CLOCK_S", pinned)
    budget = _floor_run(monkeypatch, tmp_path, auto_pilot="0")
    assert budget.wall_clock_s == float(pinned)
    assert budget.phase_wall_clock_s == float(pinned)
