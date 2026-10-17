"""Load the product DNA bundle — checksums FIRST, before anything else runs."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

from . import stop

MANIFEST = "checksum_manifest.json"


@dataclass(frozen=True)
class DnaBundle:
    path: str
    files: Dict[str, str]  # name -> text content (only checksum-verified files)
    manifest: Dict[str, Any]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(product_root: str) -> DnaBundle:
    root = Path(product_root) / "product-dna"
    if not (root / MANIFEST).is_file():
        stop.halt("dna_missing", {"path": str(root)}, "Regenerate the product DNA via the Factory.")
    try:
        manifest = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
        expected = dict(manifest.get("files") or {})
    except (OSError, json.JSONDecodeError, AttributeError) as exc:
        stop.halt("dna_unparsable", {"error": str(exc)}, "Regenerate the product DNA.")
    if manifest.get("algorithm") != "sha256" or not expected:
        stop.halt("dna_unparsable", {"manifest": "bad shape"}, "Regenerate the product DNA.")
    errors, files = [], {}
    for name, digest in sorted(expected.items()):
        f = root / name
        if not f.is_file():
            errors.append(f"missing file: {name}")
        elif _sha256(f) != digest:
            errors.append(f"checksum mismatch: {name}")
        else:
            files[name] = f.read_text(encoding="utf-8", errors="replace")
    if errors:
        stop.halt("dna_checksum_failed", {"errors": errors}, "Do not hand-edit product-dna — regenerate it via the Factory.")
    return DnaBundle(path=str(root), files=files, manifest=manifest)
