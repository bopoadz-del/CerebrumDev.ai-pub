from pathlib import Path

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.dual_registry import DualRegistryError, assert_dual_registered
from app.factory.planner import CapabilityPlanner


from tests.factory.blocks_root import real_blocks_root

ROOT = Path(__file__).resolve().parents[3]
BLOCKS = real_blocks_root()


def test_estate_blocks_dual_registered():
    ids = [
        "estate_registry",
        "estate_maintenance",
        "evidence_verifier",
        "readiness_engine",
        "portfolio_rollup",
    ]
    assert assert_dual_registered(ids, BLOCKS) == sorted(ids)


def test_unknown_block_fails_closed():
    with pytest.raises(DualRegistryError):
        assert_dual_registered(["not_a_real_block_xyz"], BLOCKS)


def test_planner_steward():
    bp = load_blueprint(ROOT / "blueprints/steward/steward.v1.yaml")
    plan = CapabilityPlanner(BLOCKS).plan(bp)
    assert plan.product_id == "projectsix"
    assert not plan.unsupported
    strategies = {c.capability_id: c.strategy for c in plan.capabilities}
    assert strategies["estate_registry"] == "COMPOSE"
    assert strategies["composed_ops_loop"] == "COMPOSE"
    assert strategies["human_authority_gate"] == "GENERATE"


def test_planner_rejects_undual_block(tmp_path):
    p = tmp_path / "bp.yaml"
    p.write_text(
        """
schema_version: product_blueprint.v1
product_id: bad-product
product_name: Bad
vertical: estate
summary: bad
capabilities:
  - id: ghost
    description: uses missing block
    block_ids: [totally_missing_block]
"""
    )
    bp = load_blueprint(p)
    with pytest.raises(DualRegistryError):
        CapabilityPlanner(BLOCKS).plan(bp)


@pytest.mark.parametrize(
    "planned, want_class",
    [
        ([], "small"),
        ([("UNSUPPORTED", [])], "small"),
        ([("MAPPED", [])], "new_platform"),
        ([("MAPPED", ["b1"])], "small"),
    ],
)
def test_budget_class_for_classifies_the_plan(planned, want_class):
    """The plan-time class: an empty/all-unsupported plan is small (the
    drop_rule mutant would divide by zero), 100% no-block is new_platform."""
    from app.factory.planner import PlannedCapability, _budget_class_for

    caps = [
        PlannedCapability(capability_id=f"c{i}", strategy=s, block_ids=b)
        for i, (s, b) in enumerate(planned)
    ]
    got_class, _share = _budget_class_for(caps)
    assert got_class == want_class


