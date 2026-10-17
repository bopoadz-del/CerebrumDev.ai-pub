"""The compliance gate must refuse, and must be impossible to route around.

Its value is AC8: one call site on the production path. Seven callers of
generate_product exist across four modules, so a gate wired at call sites
would be seven wirings and a silent bypass for the eighth.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.factory.compliance_gate import (
    BLOCK_BACKED_STRATEGIES,
    REASON_NO_BLOCK_DECLARES,
    REASON_NOT_DUAL_REGISTERED,
    REASON_NOT_PLANNED,
    REASON_UNSUPPORTED,
    ComplianceError,
    assert_compliant,
    evaluate_plan,
)
from app.factory.planner import CapabilityPlanner, PlannedCapability, ProductPlan

ARCHITECT = Path(__file__).resolve().parents[2] / "app" / "factory" / "product_architect.py"
BLUEPRINTS = Path(__file__).resolve().parents[3] / "blueprints"


def _plan(caps, *, unsupported=(), dual=()):
    return ProductPlan(
        product_id="p",
        capabilities=list(caps),
        unsupported=list(unsupported),
        dual_registered_blocks=list(dual),
    )


# -- a clean plan passes --------------------------------------------------


def test_a_coherent_plan_is_compliant():
    plan = _plan(
        [PlannedCapability("cap_a", "REUSE", ["block_a"])], dual=["block_a"]
    )
    verdict = evaluate_plan(plan)
    assert verdict.compliant is True
    assert verdict.gaps == ()
    assert verdict.checked == ("cap_a",)


def test_generate_strategies_need_no_block():
    plan = _plan([PlannedCapability("cap_a", "GENERATE", [])])
    assert evaluate_plan(plan).compliant is True


def test_adapt_without_a_block_is_not_a_gap():
    """The planner treats a blockless ADAPT as kernel generation. A gate that
    contradicts the component it guards gets switched off, not fixed."""
    assert "ADAPT" not in BLOCK_BACKED_STRATEGIES
    plan = _plan([PlannedCapability("cap_a", "ADAPT", [])])
    assert evaluate_plan(plan).compliant is True


# -- each gap fires -------------------------------------------------------


def test_a_block_backed_strategy_naming_no_block_is_a_gap():
    plan = _plan([PlannedCapability("cap_a", "REUSE", [])])
    verdict = evaluate_plan(plan)
    assert verdict.compliant is False
    assert verdict.gaps[0].reason == REASON_NO_BLOCK_DECLARES
    assert "names none" in verdict.gaps[0].detail


def test_a_block_outside_the_dual_registered_set_is_a_gap():
    plan = _plan([PlannedCapability("cap_a", "REUSE", ["ghost"])], dual=[])
    verdict = evaluate_plan(plan)
    assert verdict.compliant is False
    assert verdict.gaps[0].reason == REASON_NOT_DUAL_REGISTERED
    assert "ghost" in verdict.gaps[0].detail


def test_unsupported_capabilities_are_gaps():
    plan = _plan([], unsupported=["cap_x"])
    verdict = evaluate_plan(plan)
    assert verdict.gaps[0].reason == REASON_UNSUPPORTED


def test_a_requested_capability_missing_from_the_plan_is_a_gap():
    class _Cap:
        id = "cap_missing"

    class _BP:
        capabilities = [_Cap()]

    plan = _plan([PlannedCapability("cap_a", "GENERATE", [])])
    verdict = evaluate_plan(plan, blueprint=_BP())
    assert verdict.compliant is False
    assert verdict.gaps[0].reason == REASON_NOT_PLANNED


def test_an_unsupported_capability_counts_as_planned_for_coverage():
    """It is already reported as unsupported; reporting it twice under two
    reasons would make one gap look like two problems."""

    class _Cap:
        id = "cap_x"

    class _BP:
        capabilities = [_Cap()]

    plan = _plan([], unsupported=["cap_x"])
    reasons = [g.reason for g in evaluate_plan(plan, blueprint=_BP()).gaps]
    assert reasons == [REASON_UNSUPPORTED]


def test_every_gap_is_reported_not_only_the_first():
    """A caller fixing one gap per build learns the problem one build at a
    time."""
    plan = _plan(
        [
            PlannedCapability("cap_a", "REUSE", []),
            PlannedCapability("cap_b", "COMPOSE", ["ghost"]),
        ],
        unsupported=["cap_c"],
    )
    verdict = evaluate_plan(plan)
    assert len(verdict.gaps) == 3
    assert {g.capability_id for g in verdict.gaps} == {"cap_a", "cap_b", "cap_c"}


# -- assert_compliant -----------------------------------------------------


def test_assert_compliant_returns_a_clean_plan():
    plan = _plan([PlannedCapability("cap_a", "REUSE", ["b"])], dual=["b"])
    assert assert_compliant(plan) is plan


def test_assert_compliant_raises_with_every_reason_named():
    plan = _plan([PlannedCapability("cap_a", "REUSE", [])])
    with pytest.raises(ComplianceError) as excinfo:
        assert_compliant(plan)
    assert "cap_a" in str(excinfo.value)
    assert excinfo.value.verdict.compliant is False


# -- AC8: one call site, no parameter that skips it -----------------------


def test_generate_product_calls_the_gate_exactly_once():
    tree = ast.parse(ARCHITECT.read_text(encoding="utf-8"))
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "generate_product"
    )
    calls = [
        n for n in ast.walk(fn)
        if isinstance(n, ast.Call)
        and (getattr(n.func, "id", None) or getattr(n.func, "attr", None))
        == "assert_compliant"
    ]
    assert len(calls) == 1, (
        "the compliance gate must be called exactly once inside the single "
        "production door, not per-branch"
    )


def test_generate_product_takes_no_parameter_that_skips_the_gate():
    tree = ast.parse(ARCHITECT.read_text(encoding="utf-8"))
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "generate_product"
    )
    names = {a.arg for a in fn.args.args + fn.args.kwonlyargs}
    forbidden = {n for n in names if "skip" in n or "force" in n or "compliance" in n}
    assert forbidden == set(), f"generate_product grew a bypass parameter: {forbidden}"


# -- the real blueprints still build --------------------------------------


@pytest.mark.parametrize(
    "rel",
    [
        "examples/basic_product.yaml",
        "examples/field_ops.yaml",
        "examples/runner_smoke.yaml",
        "steward/steward.v1.yaml",
    ],
)
def test_shipped_blueprints_are_compliant(rel):
    """A gate that refuses the blueprints in the repository is not a gate,
    it is an outage."""
    from app.factory.blueprint import load_blueprint

    path = BLUEPRINTS / rel
    if not path.is_file():
        pytest.skip(reason=f"{rel} not present")
    bp = load_blueprint(path)
    plan = CapabilityPlanner(None, None).plan(bp)
    verdict = evaluate_plan(plan, blueprint=bp)
    assert verdict.compliant is True, verdict.summary()
