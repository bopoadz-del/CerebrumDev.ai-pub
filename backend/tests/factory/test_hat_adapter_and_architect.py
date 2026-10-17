"""Hat adaptation (TEK patterns) + Product Architect pipeline."""

from pathlib import Path

from app.factory.blueprint import load_blueprint
from app.factory.generator import ProductGenerator
from app.factory.hat_adapter import build_hat_manifests, build_workflows
from app.factory.planner import CapabilityPlanner
from app.factory.product_architect import architect_pipeline


from tests.factory.blocks_root import real_blocks_root

ROOT = Path(__file__).resolve().parents[3]
BLOCKS = real_blocks_root()


def test_steward_hats_adapted_from_tek_pattern():
    bp = load_blueprint(ROOT / "blueprints/steward/steward.v1.yaml")
    plan = CapabilityPlanner(BLOCKS).plan(bp)
    hats = build_hat_manifests(bp, plan)
    kinds = {h["kind"] for h in hats}
    assert "base" in kinds and "hat" in kinds
    base = next(h for h in hats if h["kind"] == "base")
    assert base["agent_id"] == "estate.base"
    assert base["extends"] is None
    assert any(h["agent_id"].startswith("estate.hat.") for h in hats)
    assert "retail." not in base["agent_id"]


def test_steward_workflows_composed():
    bp = load_blueprint(ROOT / "blueprints/steward/steward.v1.yaml")
    plan = CapabilityPlanner(BLOCKS).plan(bp)
    # The workflows are the serving kit's data (Store manifest ``hats``).
    hats = ProductGenerator(bp, plan, blocks_root=BLOCKS)._kit_hats()
    workflows = build_workflows(bp, plan, hats)
    ids = {w["workflow_id"] for w in workflows}
    assert "estate.ops_loop" in ids
    assert "estate.portfolio_rollup" in ids


def test_hat_builder_holds_no_kit_data():
    """Same plan, no kit hats: a capability is its own discipline and one
    linear workflow runs over the plan. Kit hats on invented names apply."""
    from types import SimpleNamespace as NS

    bp = NS(vertical="zorblat_yards", product_name="Zorblat Yard", human_authority=True)
    plan = NS(capabilities=[
        NS(capability_id="gate_in", block_ids=["database"], strategy="REUSE"),
        NS(capability_id="gate_out", block_ids=["database"], strategy="REUSE"),
    ])
    bare = build_hat_manifests(bp, plan)
    assert {h["discipline"] for h in bare if h["kind"] == "hat"} == {"gate_in", "gate_out"}
    assert [w["workflow_id"] for w in build_workflows(bp, plan)] == ["zorblat_yards.capability_sequence"]

    hats = {
        "disciplines": {"gate_in": "yard", "gate_out": "yard_exit"},
        "handoffs": [{"from": "yard", "to": "yard_exit", "when": "leaving"}],
        "workflows": [{
            "id": "turnaround", "when_all": ["gate_in", "gate_out"],
            "steps": [{"capability_id": "gate_in", "role": "arrive", "required": "$human_authority"}],
        }],
    }
    shaped = build_hat_manifests(bp, plan, hats)
    yard = next(h for h in shaped if h.get("discipline") == "yard")
    assert yard["handoffs"] == [{"to_agent_id": "zorblat_yards.hat.yard_exit", "when": "leaving"}]
    wf = build_workflows(bp, plan, hats)
    assert [w["workflow_id"] for w in wf] == ["zorblat_yards.turnaround"]
    assert wf[0]["steps"][0]["required"] is True


def test_generator_emits_hats_and_workflows(tmp_path):
    bp = load_blueprint(ROOT / "blueprints/steward/steward.v1.yaml")
    out = tmp_path / "steward"
    ProductGenerator(bp, blocks_root=BLOCKS, factory_commit="t", blocks_commit="t").generate(out)
    assert (out / "app" / "agents" / "manifests").exists()
    assert list((out / "app" / "agents" / "manifests").glob("*.json"))
    assert (out / "app" / "workflows" / "workflows.json").exists()
    assert (out / "frontend" / "src" / "modules").exists()


def test_architect_brief_uses_steward_golden(tmp_path, monkeypatch, stub_coder):
    # Agent manifests / hats are a TEMPLATE-path artifact; production now
    # builds through the role runner, which does not emit them (registered
    # in KNOWN_INCOMPLETE). Pin the engine so this keeps guarding the
    # template contract it was written for.
    monkeypatch.setenv("FACTORY_BUILD_ENGINE", "template")
    result = architect_pipeline(
        "Generate The Steward private estate platform",
        tmp_path / "out",
        blocks_root=BLOCKS,
    )
    assert result["ok"] is True
    assert (Path(result["generation"]["output_dir"]) / "app" / "agents" / "manifests").exists()
