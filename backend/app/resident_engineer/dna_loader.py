"""Load Product DNA as the sole product-understanding surface."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from app.product_dna.emit import DNA_BUNDLE_FILES, verify_checksum_manifest
from app.resident_engineer.injection_guard import sanitize_untrusted


class DnaLoadError(RuntimeError):
    """Raised when the Product DNA bundle cannot be trusted/loaded."""


def resolve_product_dna_dir(root: Optional[Path] = None) -> Path:
    """Prefer canonical ``product-dna/`` over legacy ``product-agent/product_dna/``."""
    base = Path(root) if root else Path.cwd()
    canonical = base / "product-dna"
    if canonical.is_dir() and (canonical / "checksum_manifest.json").is_file():
        return canonical
    legacy = base / "product-agent" / "product_dna"
    if legacy.is_dir():
        return legacy
    raise DnaLoadError(f"no product-dna bundle under {base}")


def load_product_dna(root: Optional[Path] = None, *, verify: bool = True) -> Dict[str, Any]:
    """Load DNA documents as untrusted data (sanitized strings).

    Never scans application source to infer product shape.
    """
    dna_dir = resolve_product_dna_dir(root)
    if verify and (dna_dir / "checksum_manifest.json").is_file():
        errors = verify_checksum_manifest(dna_dir)
        if errors:
            raise DnaLoadError("checksum verification failed: " + "; ".join(errors))

    bundle: Dict[str, Any] = {"_dna_dir": str(dna_dir), "files": {}}
    for name in DNA_BUNDLE_FILES:
        path = dna_dir / name
        if not path.is_file():
            # Legacy thin DNA may miss some files — record empty.
            bundle["files"][name] = None
            continue
        raw = path.read_text(encoding="utf-8")
        if name.endswith(".yaml"):
            data = yaml.safe_load(raw) or {}
        else:
            data = json.loads(raw)
        bundle["files"][name] = sanitize_untrusted(data)
    return bundle


def dna_entity_refs(bundle: Dict[str, Any]) -> Dict[str, Any]:
    """Extract entity reference handles from loaded DNA (for diagnosis)."""
    files = bundle.get("files") or {}
    actions = (files.get("action_catalog.json") or {}).get("actions") or []
    agents = (files.get("agent_catalog.json") or {}).get("agents") or []
    workflows = (files.get("workflow_catalog.json") or {}).get("workflows") or []
    caps = (files.get("capability_resolution.json") or {}).get("capabilities") or []
    return {
        "action_ids": [a.get("action_id") for a in actions if isinstance(a, dict)],
        "agent_ids": [a.get("agent_id") for a in agents if isinstance(a, dict)],
        "workflow_ids": [w.get("workflow_id") for w in workflows if isinstance(w, dict)],
        "capability_ids": [
            c.get("capability_id") for c in caps if isinstance(c, dict)
        ],
        "product_id": (files.get("generation_manifest.json") or {}).get("product_id"),
    }
