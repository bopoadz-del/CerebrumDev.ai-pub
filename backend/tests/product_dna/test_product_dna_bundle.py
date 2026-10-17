"""Product DNA Milestone 1: schemas, Steward emission, checksum verification."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from app.factory.blueprint import load_blueprint
from app.factory.generator import ProductGenerator
from app.product_dna.emit import (
    DNA_BUNDLE_FILES,
    emit_product_dna,
    verify_checksum_manifest,
)
from app.product_dna.validate import validate_dna_document

ROOT = Path(__file__).resolve().parents[3]
STEWARD_BP = ROOT / "blueprints/steward/steward.v1.yaml"


def test_all_dna_schemas_exist_and_validate_empty_honest_docs():
    """Each schema file exists; empty-array documents validate where applicable."""
    emptyish = {
        "entity_model": {"schema_version": "1.0.0", "entities": [], "relationships": []},
        "test_catalog": {"schema_version": "1.0.0", "tests": []},
        "deployment_topology": {
            "schema_version": "1.0.0",
            "targets": [],
            "services": [],
            "networks": [],
        },
        "known_limitations": {"schema_version": "1.0.0", "limitations": []},
        "change_history": {"schema_version": "1.0.0", "changes": []},
        "action_catalog": {"schema_version": "1.0.0", "actions": []},
        "agent_catalog": {"schema_version": "1.0.0", "agents": []},
        "workflow_catalog": {"schema_version": "1.0.0", "workflows": []},
    }
    for name, doc in emptyish.items():
        errors = validate_dna_document(name, doc)
        assert errors == [], (name, errors)


def test_steward_generate_emits_product_dna_bundle(tmp_path):
    bp = load_blueprint(STEWARD_BP)
    out = tmp_path / "Projectsix"
    result = ProductGenerator(
        bp,
        blocks_root=None,
        factory_commit="factory-test",
        blocks_commit="blocks-test",
    ).generate(out)

    dna = out / "product-dna"
    assert dna.is_dir()
    assert "product_dna" in result
    for name in DNA_BUNDLE_FILES:
        assert (dna / name).is_file(), name
    assert (dna / "checksum_manifest.json").is_file()

    # Schema-validate every JSON document + YAML blueprint projection
    for name in DNA_BUNDLE_FILES:
        path = dna / name
        if name.endswith(".yaml"):
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
            schema_name = "product_blueprint"
        else:
            doc = json.loads(path.read_text(encoding="utf-8"))
            schema_name = name.replace(".json", "")
        errors = validate_dna_document(schema_name, doc)
        assert errors == [], (name, errors)

    # Catalogs populated from Factory (not invented empty when data exists)
    actions = json.loads((dna / "action_catalog.json").read_text(encoding="utf-8"))
    agents = json.loads((dna / "agent_catalog.json").read_text(encoding="utf-8"))
    workflows = json.loads((dna / "workflow_catalog.json").read_text(encoding="utf-8"))
    assert len(actions["actions"]) >= 1
    assert len(agents["agents"]) >= 1
    assert len(workflows["workflows"]) >= 1

    # Entity model populated for estate vertical; other surfaces remain honest empties
    entities = json.loads((dna / "entity_model.json").read_text(encoding="utf-8"))
    tests = json.loads((dna / "test_catalog.json").read_text(encoding="utf-8"))
    topo = json.loads((dna / "deployment_topology.json").read_text(encoding="utf-8"))
    assert len(entities["entities"]) >= 5
    assert tests["tests"] == []
    assert topo["services"] == []

    # Checksum round-trip
    assert verify_checksum_manifest(dna) == []
    # Tamper detection
    lock = dna / "block_lockfile.json"
    lock.write_text(lock.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert any("checksum mismatch" in e for e in verify_checksum_manifest(dna))


def test_emit_product_dna_direct_round_trip(tmp_path):
    bp = load_blueprint(STEWARD_BP)
    gen = ProductGenerator(bp, blocks_root=None)
    meta = emit_product_dna(
        tmp_path,
        bp,
        gen.plan,
        factory_commit="f",
        blocks_commit="b",
        actions=[{"action_id": "estate.demo"}],
        agents=[],
        workflows=[],
    )
    assert len(meta["checksums"]) == len(DNA_BUNDLE_FILES)
    assert verify_checksum_manifest(tmp_path / "product-dna") == []
    manifest = json.loads(
        (tmp_path / "product-dna" / "checksum_manifest.json").read_text(encoding="utf-8")
    )
    errors = validate_dna_document("checksum_manifest", manifest)
    assert errors == []
    assert manifest["read_only"] is True
