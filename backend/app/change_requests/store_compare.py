"""Advisory Store upgrade compare — read-only; never auto-emits UPGRADE requests."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from app.factory.dual_registry import load_factory_shelf


def _load_shelf_versions(shelf_path: Optional[Path] = None) -> Dict[str, str]:
    refs = load_factory_shelf(shelf_path)
    return {block_id: ref.version for block_id, ref in refs.items()}


def compare_lockfile_to_store(
    lockfile: Mapping[str, Any],
    *,
    store_versions: Optional[Mapping[str, str]] = None,
    changelogs: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    """Compare DNA block_lockfile pins to Store/shelf versions.

    Returns advisories with recommend: ignore | later | now.
    Never writes an UPGRADE change-request.
    """
    store = dict(store_versions) if store_versions is not None else _load_shelf_versions()
    changelogs = dict(changelogs or {})
    pins = list(lockfile.get("pins") or [])
    advisories: List[Dict[str, Any]] = []

    for pin in pins:
        if not isinstance(pin, dict):
            continue
        block_id = pin.get("block_id")
        if not block_id:
            continue
        pinned = pin.get("version") or pin.get("pinned_version") or "unknown"
        available = store.get(str(block_id))
        if available is None:
            advisories.append(
                {
                    "block_id": block_id,
                    "pinned_version": pinned,
                    "available_version": None,
                    "changelog": changelogs.get(str(block_id)),
                    "status": "not_in_store",
                    "recommend": "ignore",
                }
            )
            continue
        if pinned == "unknown":
            recommend = "later"
            status = "pinned_version_unknown"
        elif pinned == available:
            recommend = "ignore"
            status = "up_to_date"
        else:
            # Naive semver-ish: if strings differ, flag upgrade available
            status = "upgrade_available"
            recommend = "now" if _is_newer(available, pinned) else "later"
        advisories.append(
            {
                "block_id": block_id,
                "pinned_version": pinned,
                "available_version": available,
                "changelog": changelogs.get(str(block_id)),
                "status": status,
                "recommend": recommend,
            }
        )

    return {
        "schema_version": "1.0.0",
        "kind": "store_upgrade_advisory",
        "blocks_commit": lockfile.get("blocks_commit"),
        "advisories": advisories,
        "auto_emits_upgrade_request": False,
        "honesty": "advisory only — human or approved process must create UPGRADE requests",
    }


def _is_newer(available: str, pinned: str) -> bool:
    """Best-effort version compare; equal/unknown → False."""
    def parts(v: str) -> List[int]:
        out: List[int] = []
        for piece in v.replace("-", ".").split("."):
            if piece.isdigit():
                out.append(int(piece))
            else:
                break
        return out or [0]

    try:
        return parts(available) > parts(pinned)
    except Exception:  # noqa: BLE001
        return available != pinned


def compare_product_dna_dir(product_root: Path, **kwargs: Any) -> Dict[str, Any]:
    """Load product-dna/block_lockfile.json and compare."""
    from app.resident_engineer.dna_loader import load_product_dna

    bundle = load_product_dna(product_root, verify=False)
    lock = (bundle.get("files") or {}).get("block_lockfile.json") or {}
    return compare_lockfile_to_store(lock, **kwargs)
