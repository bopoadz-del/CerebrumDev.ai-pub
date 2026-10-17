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


