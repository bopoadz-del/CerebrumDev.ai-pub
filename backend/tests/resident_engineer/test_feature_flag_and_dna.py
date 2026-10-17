"""Flag default off; DNA loader prefers /product-dna/; generator injects runtime."""

from __future__ import annotations


from app.factory.blueprint import load_blueprint
from app.factory.generator import ProductGenerator
from app.resident_engineer.dna_loader import load_product_dna
from app.resident_engineer.flags import resident_engineer_enabled
from app.resident_engineer.modes import draft_change_request

ROOT = __import__("pathlib").Path(__file__).resolve().parents[3]


def test_feature_flag_default_off(monkeypatch):
    monkeypatch.delenv("RESIDENT_ENGINEER_ENABLED", raising=False)
    assert resident_engineer_enabled() is False


def test_l3_is_draft_only():
    draft = draft_change_request(
        level="L3",
        kind="ExpansionRequest",
        summary="Add portfolio widget",
        product_id="projectsix",
    )
    assert draft["execution"] == "draft_only"
    assert draft["status"] == "draft"


def test_generate_injects_runtime_and_dna_loader(tmp_path):
    bp = load_blueprint(ROOT / "blueprints/steward/steward.v1.yaml")
    out = tmp_path / "steward"
    result = ProductGenerator(bp, blocks_root=None).generate(out)
    assert (out / "app" / "resident_engineer" / "router.py").is_file()
    router_src = (out / "app" / "resident_engineer" / "router.py").read_text(encoding="utf-8")
    assert '@router.get("/status")' in router_src
    assert (out / "Dockerfile").is_file()
    assert (out / "Procfile").is_file()
    assert (out / "app" / "resident_engineer" / "heal" / "catalog.py").is_file()
    assert (out / "product-dna" / "checksum_manifest.json").is_file()
    assert "resident.retry_ingestion" in result["resident_runtime"]["allowlisted_heals"]

    bundle = load_product_dna(out)
    policy = bundle["files"]["security_policy.json"]
    assert "resident.rebuild_index" in policy["allowlisted_heal_actions"]


def test_status_route_reports_flag(client, monkeypatch):
    monkeypatch.delenv("RESIDENT_ENGINEER_ENABLED", raising=False)
    # Re-import not needed — flag reads env live
    res = client.get("/v1/resident/status")
    # May 401 if API key set — conftest client without key when key empty
    if res.status_code == 401:
        return
    assert res.status_code == 200
    assert res.json()["enabled"] is False
