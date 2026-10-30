"""K-F7 / F-9c: the Store-first formula order, enforced (ORDERS_1900Z).

Owner ruling: the coder writes and finds formulas himself ONLY when nothing is
available in our Store. Resolution order for any formula, rule or figure a
capability needs:

1. ``kernel``  -- cerebrum_product_kernel universal definitions (by address);
2. ``store``   -- the Store at ``store.pin``: domain_packs/*/pack.json formula
   ids and the serving reasoning kit's invariant ids;
3. ``improvised`` -- only when neither covers it, labelled
   ``IMPROVISED_LABEL``, with a source cited in the definition row and a
   new_domain_candidates.jsonl row naming it.

Nothing here names a vertical, a pack or a kit: which pack or kit covers a
vertical is read off the Store's own manifests (``domain_id`` /
``serves_verticals``), and the kernel ids come from ``definition_index()``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from app.factory.store_kits import reasoning_kits, serving_kit, slug

IMPROVISED_LABEL = "improvised, no Store coverage"
LAYER_KERNEL = "kernel"
LAYER_STORE = "store"
LAYER_IMPROVISED = "improvised"
ORDER = (LAYER_KERNEL, LAYER_STORE, LAYER_IMPROVISED)
#: Where, in the product tree, the writer records the formulas it authored
#: and the new-domain candidates it raises (named to it in the prompt).
WRITER_DEFINITIONS_REL = "app/formulas/writer_definitions.jsonl"
WRITER_CANDIDATES_REL = "app/formulas/new_domain_candidates.jsonl"


def _store_root(store_root: Any) -> Optional[Path]:
    if store_root is not None:
        return Path(store_root)
    try:
        from app.factory.blocks_source import resolve_blocks_root

        return Path(resolve_blocks_root())
    except Exception:  # noqa: BLE001 -- no Store: no coverage, never invented
        return None


def _pack_serves(manifest: Mapping[str, Any], want: str) -> bool:
    names = {slug(manifest.get("domain_id"))}
    names |= {slug(v) for v in (manifest.get("serves_verticals") or [])}
    return bool(want) and want in names


def store_coverage_index(vertical: Any, store_root: Any = None) -> List[Dict[str, str]]:
    """Store formula/invariant ids that cover ``vertical``, sorted by id.

    Pack formulas: every ``domain_packs/<p>/pack.json`` whose manifest
    ``domain_id`` or ``serves_verticals`` is the vertical. Kit invariants: the
    reasoning kit :func:`serving_kit` picks for the vertical. Empty when the
    Store has nothing -- absence stays absence.
    """
    root = _store_root(store_root)
    want = slug(vertical)
    if root is None or not want:
        return []
    rows: List[Dict[str, str]] = []
    for path in sorted((root / "domain_packs").glob("*/pack.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or not _pack_serves(data.get("manifest") or {}, want):
            continue
        rel = path.relative_to(root).as_posix()
        for spec in data.get("formulas") or []:
            fid = str((spec or {}).get("formula_id") or "").strip()
            if fid:
                rows.append({"id": fid, "kind": "pack_formula", "source": rel})
    kits = reasoning_kits(root)
    kit = serving_kit(want, kits)
    if kit:
        import yaml

        inv_path = root / "app" / "blocks" / kit / "invariants.yaml"
        try:
            inv = yaml.safe_load(inv_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            inv = {}
        rel = inv_path.relative_to(root).as_posix()
        for rec in (inv.get("invariants") or []) if isinstance(inv, dict) else []:
            iid = str((rec or {}).get("id") or "").strip()
            if iid:
                rows.append({"id": iid, "kind": "kit_invariant", "source": rel})
    return sorted(rows, key=lambda r: (r["id"], r["kind"]))



def render_resolution_section(
    kernel_index: Sequence[Mapping[str, Any]],
    store_index: Sequence[Mapping[str, Any]],
    store_sha: Optional[str] = None,
) -> str:
    """The prompt block listing, in resolution order, what already exists."""
    lines = ["FORMULA SOURCES, IN RESOLUTION ORDER (use the first that covers it):",
             "1. KERNEL definitions (cerebrum_product_kernel, by address):"]
    kernel = sorted(kernel_index or [], key=lambda r: str(r.get("id")))
    lines += [f"   - {r.get('id')} @ {r.get('address') or '(no address)'}" for r in kernel] or ["   (none)"]
    pin = f" at store.pin {store_sha}" if store_sha else " (store.pin unreadable)"
    lines.append(f"2. STORE coverage for this vertical{pin}:")
    store = sorted(store_index or [], key=lambda r: (str(r.get("id")), str(r.get("kind"))))
    lines += [f"   - {r.get('id')} [{r.get('kind')}] {r.get('source')}" for r in store] or ["   (none)"]
    lines.append(f'3. IMPROVISED -- only for what 1 and 2 do not cover; label "{IMPROVISED_LABEL}".')
    return "\n".join(lines) + "\n\n"
