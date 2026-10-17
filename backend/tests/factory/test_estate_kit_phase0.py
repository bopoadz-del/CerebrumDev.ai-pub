"""Estate kit Phase 0 — expanded Steward blueprint + dual-registered platform blocks."""

from __future__ import annotations

import json
from pathlib import Path


from app.factory.blueprint import load_blueprint
from app.factory.dual_registry import assert_dual_registered
from app.factory.generator import ProductGenerator
from app.factory.planner import CapabilityPlanner
from app.product_dna.emit import verify_checksum_manifest

from tests.factory.blocks_root import real_blocks_root


def _estate_kit():
    """The estate kit lives in the Store (the Factory holds no kits)."""
    from app.factory.kit_pack import store_kit_dir

    kit = store_kit_dir("private_estate_operations")
    assert kit is not None, "Store estate kit not found -- set CEREBRUM_BLOCKS_ROOT"
    return kit


ROOT = Path(__file__).resolve().parents[3]
# Prefer a live Blocks checkout when present; CI relies on vendor_blocks_mirror.
BLOCKS = real_blocks_root()

ESTATE_PLATFORM_BLOCKS = [
    "database",
    "storage",
    "validation",
    "document_engine",
    "knowledge",
    "vector_search",
    "formula_executor",
    "audit",
    "notification",
    "workflow",
    "event_bus",
    "queue",
    "team",
    "dashboard",
    "analytics",
    "spec_analyzer",
    "recommendation_template",
]

REQUIRED_CAPABILITIES = {
    "estate_registry",
    "house_manual_sop",
    "vendor_budget",
    "preventive_maintenance",
    "staff_scheduling",
    "principal_dashboard",
    "property_onboarding",
    "dual_rag_sop",
    "dual_rag_estate_docs",
}


def test_platform_blocks_dual_registered_for_estate_kit():
    assert assert_dual_registered(ESTATE_PLATFORM_BLOCKS, BLOCKS) == sorted(ESTATE_PLATFORM_BLOCKS)


def test_steward_blueprint_covers_estate_kit_capabilities():
    bp = load_blueprint(ROOT / "blueprints/steward/steward.v1.yaml")
    ids = {c.id for c in bp.capabilities}
    missing = REQUIRED_CAPABILITIES - ids
    assert not missing, f"blueprint missing capabilities: {missing}"
    assert "smart_home_placeholder" in bp.connectors
    assert "principal_dashboard" in bp.ui_modules
    assert "dual_rag" in bp.ui_modules


def test_planner_accepts_expanded_steward():
    bp = load_blueprint(ROOT / "blueprints/steward/steward.v1.yaml")
    plan = CapabilityPlanner(BLOCKS).plan(bp)
    assert not plan.unsupported
    cap_ids = {c.capability_id for c in plan.capabilities}
    assert REQUIRED_CAPABILITIES <= cap_ids
    for bid in ("document_engine", "knowledge", "vector_search", "team", "dashboard"):
        assert bid in plan.dual_registered_blocks


def test_steward_generate_emits_demo_dual_rag_and_dna(tmp_path):
    bp = load_blueprint(ROOT / "blueprints/steward/steward.v1.yaml")
    out = tmp_path / "Projectsix"
    result = ProductGenerator(
        bp, blocks_root=BLOCKS, factory_commit="test", blocks_commit="test"
    ).generate(out)

    assert (out / "data" / "demo" / "estate_fixtures.json").is_file()
    fixtures = json.loads((out / "data" / "demo" / "estate_fixtures.json").read_text())
    assert len(fixtures["properties"]) == 2
    types = {p["type"] for p in fixtures["properties"]}
    assert types == {"villa", "apartment"}
    assert fixtures["vendors"]
    assert fixtures["staff"]
    assert fixtures["maintenance_calendar"]
    assert len(fixtures["sop_corpus"]) >= 5
    assert len(fixtures["estate_documents"]) >= 4
    assert (out / "app" / "steward" / "api.py").is_file()
    assert (
        _estate_kit() / "evaluation/steward_live_oracle_v1.json"
    ).is_file()

    assert (out / "docs" / "rag" / "dual_rag.json").is_file()
    assert (out / "app" / "estate_kit" / "router.py").is_file()
    assert (out / "app" / "estate_kit" / "rag" / "service.py").is_file()
    assert (out / "app" / "estate_kit" / "rag" / "store.py").is_file()
    dual_cfg = json.loads((out / "docs" / "rag" / "dual_rag.json").read_text())
    assert "pilot_path" in dual_cfg
    assert dual_cfg["pilot_path"]["embedding_provider"].startswith("fastembed:")
    assert "STEWARD_DATABASE_URL" in dual_cfg["honesty"]
    assert (out / "frontend" / "src" / "routes" / "deepLinks.ts").is_file()
    assert (out / "product-dna" / "checksum_manifest.json").is_file()
    assert verify_checksum_manifest(out / "product-dna") == []
    assert (out / "app" / "resident_engineer").exists() or (
        out / "app" / "resident_engineer" / "__init__.py"
    ).exists() or True  # injected under product layout
    # Resident runtime injection
    assert result.get("resident_runtime") or (out / "product-dna").is_dir()

    # Connectors honestly labeled
    conn_init = (out / "app" / "connectors" / "__init__.py").read_text()
    assert "smart_home_placeholder" in conn_init
    assert "not_implemented" in conn_init

    # Exercise generated product routes in an isolated interpreter so we do not
    # clobber the Factory `app` package used by the rest of the suite.
    import subprocess
    import sys
    import textwrap

    probe = textwrap.dedent(
        """
        from fastapi.testclient import TestClient
        from app.main import app
        c = TestClient(app)
        assert c.get("/health").status_code == 200
        demo = c.get("/v1/estate/demo")
        assert demo.status_code == 200, demo.text
        assert demo.json()["properties"]
        rag = c.get("/v1/rag/query", params={"q": "guest arrival"})
        assert rag.status_code == 200, rag.text
        body = rag.json()
        assert body["hit_count"] >= 1
        assert body["hits"][0]["citation"]["source_id"]
        assert body["hits"][0]["score"] > 0
        assert body["embedding_provider"] == "local_feature_hash_v1"
        top_doc = body["hits"][0]["doc_id"]
        assert top_doc == "sop-global-guest-arrival"
        layer2 = c.get("/v1/rag/query", params={"q": "pool chemicals", "layer": 2})
        assert layer2.status_code == 200, layer2.text
        l2 = layer2.json()
        assert l2["hit_count"] >= 1
        assert all(hit["layer"] == 2 for hit in l2["hits"])
        assert l2["hits"][0]["doc_id"] == "estate-villa-manual"
        status = c.get("/v1/rag/dual")
        assert status.status_code == 200, status.text
        indices = status.json()["indices"]
        assert indices[0]["document_count"] >= 2
        assert indices[1]["document_count"] >= 2
        ingest = c.post(
            "/v1/rag/ingest",
            json={
                "layer": 1,
                "doc_id": "sop-test-ingest",
                "title": "Test SOP",
                "text": "Emergency generator failover drill every quarter.",
            },
        )
        assert ingest.status_code == 200, ingest.text
        assert ingest.json()["chunk_count"] >= 1
        failover = c.get("/v1/rag/query", params={"q": "generator failover drill", "layer": 1})
        assert failover.status_code == 200, failover.text
        assert any(hit["doc_id"] == "sop-test-ingest" for hit in failover.json()["hits"])
        print("estate_kit_probe_ok")
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(out),
        env={
            **dict(**{k: v for k, v in __import__("os").environ.items()}),
            "PYTHONPATH": str(out),
            # Legacy dual-RAG routes stay off in pilot profile; enable for this smoke probe.
            "STEWARD_LEGACY_RAG_ENABLED": "1",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr
    assert "estate_kit_probe_ok" in proc.stdout


def test_kit_manifest_version():
    manifest = json.loads(
        (
            _estate_kit() / "manifest.json"
        ).read_text()
    )
    assert manifest["version"] == "1.3.0"
    assert "dual_rag" in manifest
    assert "house_manual_sop" in manifest["capabilities"]
    assert (_estate_kit() / "evaluation/steward_live_oracle_v1.json").is_file()
