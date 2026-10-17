"""Emit the read-only ``/product-dna/`` bundle from Factory-known data.

Unknown fields are explicit empty arrays/objects — never invented content.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

import yaml

from app.factory.blueprint import ProductBlueprint
from app.factory.planner import ProductPlan

try:
    from app.resident_engineer.heal.catalog import ALLOWLISTED_HEAL_ACTIONS
except Exception:  # pragma: no cover - early import safety
    ALLOWLISTED_HEAL_ACTIONS = ()

DNA_SCHEMA_VERSION = "1.0.0"

# Canonical 15-file bundle (plus checksum_manifest.json written after).
DNA_BUNDLE_FILES: tuple[str, ...] = (
    "product_blueprint.yaml",
    "generation_manifest.json",
    "capability_resolution.json",
    "source_provenance.json",
    "block_lockfile.json",
    "architecture.json",
    "entity_model.json",
    "action_catalog.json",
    "agent_catalog.json",
    "workflow_catalog.json",
    "security_policy.json",
    "deployment_topology.json",
    "test_catalog.json",
    "known_limitations.json",
    "change_history.json",
)

CHECKSUM_MANIFEST = "checksum_manifest.json"


def _write_json(path: Path, data: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_yaml(path: Path, data: Any) -> None:
    path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _kit_entity_model(vertical: str, blocks_root: Optional[Path] = None) -> Dict[str, Any]:
    """Entity DNA the Store kit serving this vertical declares, if any.

    The kit names its own entity model (``product_surface.entity_model``);
    the Factory holds none.
    """
    from app.factory.kit_pack import store_kit_dir
    from app.factory.store_kits import domain_kits, serving_kit

    empty = {"schema_version": DNA_SCHEMA_VERSION, "entities": [], "relationships": []}
    kits = domain_kits(blocks_root)
    kit_id = serving_kit(vertical, kits)
    kit_dir = store_kit_dir(kit_id, blocks_root) if kit_id else None
    rel = ((kits.get(kit_id) or {}).get("product_surface") or {}).get("entity_model")
    if kit_dir is None or not rel:
        return empty
    kit_path = kit_dir / str(rel)
    if not kit_path.is_file():
        return empty
    data = json.loads(kit_path.read_text(encoding="utf-8"))
    data.setdefault("schema_version", DNA_SCHEMA_VERSION)
    return data


def build_dna_documents(
    blueprint: ProductBlueprint,
    plan: ProductPlan,
    *,
    factory_commit: str = "unknown",
    blocks_commit: str = "unknown",
    actions: Optional[Sequence[Mapping[str, Any]]] = None,
    agents: Optional[Sequence[Mapping[str, Any]]] = None,
    workflows: Optional[Sequence[Mapping[str, Any]]] = None,
    change_events: Optional[Sequence[Mapping[str, Any]]] = None,
    product_dna_version: str = "1.0.0",
    pin_versions: Optional[Mapping[str, str]] = None,
    blocks_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Build in-memory DNA documents from Factory data only."""
    bp = blueprint.model_dump(mode="json")
    plan_dict = plan.to_dict()
    action_list = list(actions or [])
    agent_list = list(agents or [])
    workflow_list = list(workflows or [])
    changes = list(change_events or [])
    versions = dict(pin_versions or {})
    entity_model = _kit_entity_model(blueprint.vertical, blocks_root)

    return {
        "product_blueprint.yaml": {
            "schema_version": DNA_SCHEMA_VERSION,
            **bp,
        },
        "generation_manifest.json": {
            "schema_version": DNA_SCHEMA_VERSION,
            "product_id": blueprint.product_id,
            "product_dna_version": product_dna_version,
            "factory_scenario": blueprint.factory_scenario.value
            if hasattr(blueprint.factory_scenario, "value")
            else str(blueprint.factory_scenario),
            "ui_modules": list(blueprint.ui_modules or []),
            "connectors": list(blueprint.connectors or []),
            "factory_commit": factory_commit,
            "blocks_commit": blocks_commit,
        },
        "capability_resolution.json": {
            "schema_version": DNA_SCHEMA_VERSION,
            "capabilities": list(plan_dict.get("capabilities") or []),
            "dual_registered_blocks": list(plan.dual_registered_blocks),
            "unsupported": list(plan.unsupported),
            "notes": [],
        },
        "source_provenance.json": {
            "schema_version": DNA_SCHEMA_VERSION,
            "factory_commit": factory_commit,
            "blocks_commit": blocks_commit,
            "sources": [],
        },
        "block_lockfile.json": {
            "schema_version": DNA_SCHEMA_VERSION,
            "blocks_commit": blocks_commit,
            "factory_commit": factory_commit,
            "pins": [
                {
                    "block_id": bid,
                    "source": "dual_registry",
                    **({"version": versions[bid]} if bid in versions else {}),
                }
                for bid in sorted(plan.dual_registered_blocks)
            ],
        },
        "architecture.json": {
            "schema_version": DNA_SCHEMA_VERSION,
            "product_id": blueprint.product_id,
            "vertical": blueprint.vertical,
            "ui_modules": list(blueprint.ui_modules or []),
            "connectors": list(blueprint.connectors or []),
            "capabilities": [
                {
                    "capability_id": c.capability_id,
                    "strategy": c.strategy,
                    "block_ids": list(c.block_ids),
                }
                for c in plan.capabilities
            ],
            "layers": [],
        },
        "entity_model.json": entity_model,
        "action_catalog.json": {
            "schema_version": DNA_SCHEMA_VERSION,
            "actions": action_list,
        },
        "agent_catalog.json": {
            "schema_version": DNA_SCHEMA_VERSION,
            "agents": agent_list,
        },
        "workflow_catalog.json": {
            "schema_version": DNA_SCHEMA_VERSION,
            "workflows": workflow_list,
        },
        "security_policy.json": {
            "schema_version": DNA_SCHEMA_VERSION,
            "human_authority": bool(blueprint.human_authority),
            "pii_in_learning": False,
            "boundaries": [],
            "tool_permissions": [],
            "allowlisted_heal_actions": list(ALLOWLISTED_HEAL_ACTIONS),
        },
        "deployment_topology.json": {
            "schema_version": DNA_SCHEMA_VERSION,
            "targets": [],
            "services": [],
            "networks": [],
        },
        "test_catalog.json": {
            "schema_version": DNA_SCHEMA_VERSION,
            "tests": [],
        },
        "known_limitations.json": {
            "schema_version": DNA_SCHEMA_VERSION,
            "limitations": [
                "Resident Engineer starts APPRENTICE — dual certification pending",
                "Minor repairs require predefined catalog contracts",
                "Product DNA deployment_topology/test_catalog are empty until Factory owns that data",
                "Entity model stubs populated for estate vertical; work_order/budget/staff tables deferred",
                "Migration 0001 still uses create_all — residual G5 gap documented in 0001 revision",
            ],
        },
        "change_history.json": {
            "schema_version": DNA_SCHEMA_VERSION,
            "changes": changes,
        },
    }


def emit_product_dna(
    output_dir: Path | str,
    blueprint: ProductBlueprint,
    plan: ProductPlan,
    *,
    factory_commit: str = "unknown",
    blocks_commit: str = "unknown",
    actions: Optional[Sequence[Mapping[str, Any]]] = None,
    agents: Optional[Sequence[Mapping[str, Any]]] = None,
    workflows: Optional[Sequence[Mapping[str, Any]]] = None,
    change_events: Optional[Sequence[Mapping[str, Any]]] = None,
    product_dna_version: str = "1.0.0",
    pin_versions: Optional[Mapping[str, str]] = None,
    blocks_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Write ``product-dna/`` under ``output_dir`` with checksum manifest.

    Returns metadata including checksums. Bundle is intended read-only inside
    the generated product (checksum_manifest.json is the integrity surface).
    """
    out = Path(output_dir).resolve()
    dna = out / "product-dna"
    dna.mkdir(parents=True, exist_ok=True)

    docs = build_dna_documents(
        blueprint,
        plan,
        factory_commit=factory_commit,
        blocks_commit=blocks_commit,
        actions=actions,
        agents=agents,
        workflows=workflows,
        change_events=change_events,
        product_dna_version=product_dna_version,
        pin_versions=pin_versions,
        blocks_root=blocks_root,
    )

    for filename in DNA_BUNDLE_FILES:
        payload = docs[filename]
        path = dna / filename
        if filename.endswith(".yaml"):
            _write_yaml(path, payload)
        else:
            _write_json(path, payload)

    checksums = {name: _sha256_file(dna / name) for name in DNA_BUNDLE_FILES}
    manifest = {
        "schema_version": DNA_SCHEMA_VERSION,
        "algorithm": "sha256",
        "files": checksums,
        "read_only": True,
    }
    _write_json(dna / CHECKSUM_MANIFEST, manifest)

    # README for operators — not part of the 15-file contract.
    (dna / "README.md").write_text(
        "# Product DNA (read-only)\n\n"
        "This bundle is the sole product-understanding surface for Resident Mode.\n"
        "Do not edit by hand. Regenerate via CerebrumDev Factory.\n"
        "Integrity: verify `checksum_manifest.json` (sha256) against each file.\n",
        encoding="utf-8",
    )

    return {
        "path": str(dna),
        "files": list(DNA_BUNDLE_FILES),
        "checksums": checksums,
        "manifest": manifest,
    }


def verify_checksum_manifest(dna_dir: Path | str) -> List[str]:
    """Return errors if checksum_manifest does not match on-disk files."""
    root = Path(dna_dir)
    manifest_path = root / CHECKSUM_MANIFEST
    if not manifest_path.is_file():
        return [f"missing {CHECKSUM_MANIFEST}"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    errors: List[str] = []
    files: Dict[str, str] = dict(manifest.get("files") or {})
    for name in DNA_BUNDLE_FILES:
        path = root / name
        if not path.is_file():
            errors.append(f"missing file: {name}")
            continue
        expected = files.get(name)
        if not expected:
            errors.append(f"manifest missing entry: {name}")
            continue
        actual = _sha256_file(path)
        if actual != expected:
            errors.append(f"checksum mismatch: {name}")
    return errors
