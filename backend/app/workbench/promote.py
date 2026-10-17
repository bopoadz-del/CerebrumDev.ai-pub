"""Promotion via Factory packager ONLY — staging/community tier.

Workbench has no deploy credentials. Certified tier still requires chain-approve.
Tampered candidates are rejected via checksum before packaging.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from app.factory.blueprint import ProductBlueprint
from app.factory.generator import ProductGenerator
from app.product_dna.emit import verify_checksum_manifest
from app.workbench.candidate import verify_candidate_checksum
from app.workbench.live_audit import AuditRejected, validate_audit_artifact
from app.workbench.sandbox import workbench_root


class PromotionRejected(ValueError):
    """Candidate cannot be promoted."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def staging_dir(product_id: str, version: str) -> Path:
    root = workbench_root() / "staging" / product_id / version
    root.mkdir(parents=True, exist_ok=True)
    return root


def promote_via_packager(
    *,
    item: Dict[str, Any],
    candidate: Dict[str, Any],
    product_root: Path,
    actor: str = "owner",
) -> Dict[str, Any]:
    """Hand a gate_passed candidate to ProductGenerator → staging/community.

    Never deploys. Never writes to Store. Never sets certified trust tier.
    """
    if item.get("state") != "gate_passed":
        raise PromotionRejected(f"state must be gate_passed, got {item.get('state')}")

    artifact_ref = item.get("audit_artifact") or candidate.get("audit_artifact")
    try:
        if not artifact_ref:
            raise AuditRejected("no audit_artifact reference on the request")
        validate_audit_artifact(Path(artifact_ref))
    except AuditRejected as exc:
        raise PromotionRejected(f"audit_missing_or_stale: {exc}") from exc

    if not verify_candidate_checksum(candidate):
        raise PromotionRejected("tampered candidate — checksum failure at packager")

    mutation = candidate.get("mutation") or {}
    bp_data = mutation.get("blueprint")
    if not bp_data:
        raise PromotionRejected("candidate missing blueprint mutation")

    try:
        blueprint = ProductBlueprint.model_validate(bp_data)
    except Exception as exc:  # noqa: BLE001
        raise PromotionRejected(f"invalid blueprint: {exc}") from exc

    version = str(
        candidate.get("product_dna_version")
        or mutation.get("product_dna_version")
        or "1.0.1"
    )
    pin_versions = dict(
        candidate.get("pin_versions") or mutation.get("pin_versions") or {}
    )

    out = staging_dir(blueprint.product_id, version)
    # Ensure no deploy creds influence generation
    for key in ("RENDER_API_KEY", "GITHUB_TOKEN", "DEPLOY_REPO_URL", "STORE_WRITE_TOKEN"):
        os.environ.pop(key, None)

    gen = ProductGenerator(
        blueprint,
        blocks_root=None,
        product_dna_version=version,
        pin_versions=pin_versions,
        factory_commit=os.getenv("FACTORY_COMMIT", "workbench"),
        blocks_commit=os.getenv("BLOCKS_COMMIT", "pinned"),
    )
    meta = gen.generate(out, clean=True)

    dna_dir = out / "product-dna"
    checksum_errors = verify_checksum_manifest(dna_dir)
    if checksum_errors:
        raise PromotionRejected(f"packager checksum failure: {checksum_errors}")

    manifest = json.loads((dna_dir / "generation_manifest.json").read_text(encoding="utf-8"))
    lockfile = json.loads((dna_dir / "block_lockfile.json").read_text(encoding="utf-8"))

    record = {
        "schema_version": "1.0.0",
        "promoted_at": _now(),
        "actor": actor,
        "request_id": item.get("request_id"),
        "product_id": blueprint.product_id,
        "product_dna_version": version,
        "output_dir": str(out),
        "trust_tier": "staging/community",
        "certified": False,
        "deployed": False,
        "store_published": False,
        "packager": "ProductGenerator",
        "inputs_hash": meta.get("inputs_hash"),
        "generation_manifest": {
            "product_dna_version": manifest.get("product_dna_version"),
            "factory_scenario": manifest.get("factory_scenario"),
        },
        "block_lockfile_pins": lockfile.get("pins"),
        "candidate_checksum": candidate.get("checksum"),
        "deploy_credentials": False,
        "honesty": (
            "staging/community only — certified tier requires existing chain-approve; "
            "workbench has no deploy credentials"
        ),
    }

    # Write promotion receipt beside the package
    receipt = out / "STAGING_PROMOTION.json"
    receipt.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return record
