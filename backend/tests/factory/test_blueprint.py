import pytest
from pathlib import Path

from app.factory.blueprint import BlueprintError, load_blueprint


ROOT = Path(__file__).resolve().parents[3]


def test_basic_blueprint_loads():
    bp = load_blueprint(ROOT / "blueprints/examples/basic_product.yaml")
    assert bp.schema_version == "product_blueprint.v1"
    assert bp.product_id == "basic-factory-smoke"


def test_steward_blueprint_loads():
    bp = load_blueprint(ROOT / "blueprints/steward/steward.v1.yaml")
    assert bp.product_id == "projectsix"
    assert bp.edge_profile == "edge_on_prem"
    assert bp.human_authority is True


def test_invalid_schema_fails_closed(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("schema_version: nope\nproduct_id: x\nproduct_name: X\nvertical: v\nsummary: s\ncapabilities: [{id: a, description: d}]\n")
    with pytest.raises(BlueprintError):
        load_blueprint(p)
