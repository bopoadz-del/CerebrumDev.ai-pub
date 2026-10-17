"""The UNSUPPORTED gate is an invariant, not a default.

``plan()`` used to accept ``fail_on_unsupported=True``. No caller ever passed
it, so nothing broke by removing it -- which is the point: a gate that any
caller can switch off with one keyword is held shut by discipline, and
discipline is not a mechanism. These tests fail if the keyword returns, and
if a tolerated plan can reach either build engine.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.dual_registry import DualRegistryError
from app.factory.planner import CapabilityPlanner, ProductPlan, assert_generatable

APP = Path(__file__).resolve().parents[2] / "app"


def _blueprint(tmp_path: Path, *, block: str = "totally_missing_block") -> object:
    p = tmp_path / "bp.yaml"
    p.write_text(
        "schema_version: product_blueprint.v1\n"
        "product_id: gate-probe\n"
        "product_name: Gate Probe\n"
        "vertical: estate\n"
        "summary: probe\n"
        "capabilities:\n"
        "  - id: ghost\n"
        "    description: uses a block that is not dual-registered\n"
        f"    block_ids: [{block}]\n",
        encoding="utf-8",
    )
    return load_blueprint(p)


# --------------------------------------------------------------------------
# The keyword is gone and must stay gone.
# --------------------------------------------------------------------------


def test_plan_takes_no_bypass_keyword():
    params = inspect.signature(CapabilityPlanner.plan).parameters
    assert "fail_on_unsupported" not in params, (
        "the UNSUPPORTED bypass was reintroduced; plan() must not accept a "
        "keyword that disables its own gate"
    )
    assert set(params) == {"self", "blueprint"}


def test_no_source_file_passes_the_bypass():
    """Grep is the test here on purpose: the signature check above cannot see
    a call whose planner is built dynamically, so the name itself is banned.

    ``planner.py`` is exempt because it documents the removal by name, and
    naming what was removed is worth more than a uniform rule -- the
    signature test covers that file directly.
    """
    exempt = {Path("factory/planner.py")}
    offenders = [
        str(f.relative_to(APP))
        for f in APP.rglob("*.py")
        if "__pycache__" not in f.parts
        and f.relative_to(APP) not in exempt
        and "fail_on_unsupported" in f.read_text(encoding="utf-8", errors="ignore")
    ]
    assert offenders == [], f"fail_on_unsupported referenced in: {offenders}"


# --------------------------------------------------------------------------
# plan() refuses; survey() reports.
# --------------------------------------------------------------------------


def test_plan_refuses_unsupported(tmp_path):
    with pytest.raises(DualRegistryError) as excinfo:
        CapabilityPlanner(None, None).plan(_blueprint(tmp_path))
    assert "fail closed" in str(excinfo.value)


def test_survey_reports_instead_of_raising(tmp_path):
    plan = CapabilityPlanner(None, None).survey(_blueprint(tmp_path))
    assert plan.unsupported == ["ghost"]
    assert plan.fail_closed is False


def test_a_planned_plan_is_marked_fail_closed():
    plan = ProductPlan(product_id="x", capabilities=[])
    assert plan.fail_closed is True
    assert plan.to_dict()["fail_closed"] is True


# --------------------------------------------------------------------------
# A survey plan cannot reach a builder.
# --------------------------------------------------------------------------


def test_assert_generatable_refuses_a_survey_plan(tmp_path):
    survey = CapabilityPlanner(None, None).survey(_blueprint(tmp_path))
    with pytest.raises(DualRegistryError) as excinfo:
        assert_generatable(survey)
    assert "survey plan" in str(excinfo.value)
    assert "ghost" in str(excinfo.value)


def test_assert_generatable_passes_a_real_plan():
    plan = ProductPlan(product_id="x", capabilities=[])
    assert assert_generatable(plan) is plan


def test_template_engine_refuses_an_injected_survey_plan(tmp_path):
    from app.factory.generator import ProductGenerator

    bp = _blueprint(tmp_path)
    survey = CapabilityPlanner(None, None).survey(bp)
    with pytest.raises(DualRegistryError):
        ProductGenerator(bp, plan=survey)


def test_runner_engine_refuses_an_injected_survey_plan(tmp_path):
    from app.factory.build.runner import RoleRunner

    bp = _blueprint(tmp_path)
    survey = CapabilityPlanner(None, None).survey(bp)
    with pytest.raises(DualRegistryError):
        RoleRunner(bp, workspace=tmp_path / "ws", plan=survey)
