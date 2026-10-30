"""Client submission of L3/L4 overlay definition sets (K-F5).

A tenant submits an overlay SET ``{"set_id": str, "definitions": [...]}``. The
set is validated by the kernel's own :func:`resolve_definitions` together with
every set the tenant already stored, so the refusal rules (an undeclared
shadow of a base id, an override without provenance/reason, two sets claiming
one id) are the kernel's, never a bespoke path. A refused set raises
:class:`PrecedenceError` and is NOT stored. An accepted set is persisted per
tenant under ``tenant_uploads_dir(tenant)/overlays/overlays.json`` (the same
isolation boundary as the tenant's uploads and parse jobs). Re-submitting a
``set_id`` replaces that tenant's earlier version of the set.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List

from app.cerebrum_product_kernel.formulas import PrecedenceError, resolve_definitions

from .object_store import tenant_uploads_dir

__all__ = ["PrecedenceError", "OverlayShapeError", "submit_overlay", "list_overlays", "tenant_overlays"]


class OverlayShapeError(ValueError):
    """The submitted body is not an overlay set at all."""


def _store_path(tenant_id: str) -> Path:
    return tenant_uploads_dir(tenant_id) / "overlays" / "overlays.json"


def tenant_overlays(tenant_id: str) -> List[Dict[str, Any]]:
    """The tenant's stored overlay sets, in submission order ([] if none)."""
    path = _store_path(tenant_id)
    if not path.is_file():
        return []
    return json.loads(path.read_text(encoding="utf-8"))["sets"]


def _write(tenant_id: str, sets: List[Dict[str, Any]]) -> None:
    path = _store_path(tenant_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"sets": sets}, fh, indent=2, sort_keys=True)
    os.replace(tmp, path)


def _check_shape(overlay: Any) -> None:
    if not isinstance(overlay, dict):
        raise OverlayShapeError("overlay must be an object")
    if not isinstance(overlay.get("set_id"), str) or not overlay["set_id"].strip():
        raise OverlayShapeError("overlay.set_id must be a non-empty string")
    defs = overlay.get("definitions")
    if not isinstance(defs, list) or not defs or not all(isinstance(d, dict) for d in defs):
        raise OverlayShapeError("overlay.definitions must be a non-empty list of objects")


def submit_overlay(tenant_id: str, overlay: Dict[str, Any]) -> Dict[str, Any]:
    """Validate ``overlay`` against base + the tenant's other sets, then store it."""
    _check_shape(overlay)
    others = [s for s in tenant_overlays(tenant_id) if s["set_id"] != overlay["set_id"]]
    candidate = others + [overlay]
    resolved = resolve_definitions(overlays=candidate)  # PrecedenceError propagates
    _write(tenant_id, candidate)
    ids = [d["id"] for d in overlay["definitions"]]
    return {
        "set_id": overlay["set_id"],
        "definitions": ids,
        "overrides": {i: resolved[i].supersedes for i in ids if resolved[i].supersedes},
        "sets_stored": len(candidate),
    }


def list_overlays(tenant_id: str) -> Dict[str, Any]:
    sets = tenant_overlays(tenant_id)
    return {"sets": sets, "count": len(sets)}
