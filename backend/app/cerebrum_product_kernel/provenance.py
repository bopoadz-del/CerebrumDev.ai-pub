"""Provenance helpers emitted into generated products."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict


def build_provenance(
    *,
    product_id: str,
    blueprint_id: str,
    factory_commit: str,
    blocks_commit: str,
    plan: Dict[str, Any],
    inputs_hash: str,
) -> Dict[str, Any]:
    return {
        "blueprint_id": blueprint_id,
        "blocks_commit": blocks_commit,
        "factory": "CerebrumDev.ai",
        "factory_commit": factory_commit,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "inputs_hash": inputs_hash,
        "kernel_version": "1.0.0",
        "plan": plan,
        "product_id": product_id,
        "schema": "factory_provenance.v1",
    }


def write_provenance(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def hash_tree(root: Path, ignore_names: set[str] | None = None) -> str:
    ignore = ignore_names or {".git", "__pycache__", "provenance.json", ".pytest_cache"}
    h = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in ignore for part in path.parts):
            continue
        rel = path.relative_to(root).as_posix()
        h.update(rel.encode())
        h.update(path.read_bytes())
    return h.hexdigest()
