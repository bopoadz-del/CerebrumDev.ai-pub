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
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

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
#: The Factory's record of this pass's formula findings (rework reads it).
FORMULA_FINDINGS_REL = ".factory/writer_formula_findings.json"


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


def _ids(index: Iterable[Mapping[str, Any]]) -> set:
    return {str(r.get("id") or "").strip() for r in index or []} - {""}


def resolve_layer(
    ident: str,
    kernel_index: Sequence[Mapping[str, Any]],
    store_index: Sequence[Mapping[str, Any]],
) -> Dict[str, str]:
    """Which layer must supply ``ident``: kernel first, then Store, then
    improvisation (labelled). Kernel wins when both cover it."""
    ident = str(ident or "").strip()
    if ident in _ids(kernel_index):
        return {"id": ident, "layer": LAYER_KERNEL, "label": ""}
    if ident in _ids(store_index):
        return {"id": ident, "layer": LAYER_STORE, "label": ""}
    return {"id": ident, "layer": LAYER_IMPROVISED, "label": IMPROVISED_LABEL}


def writer_formula_errors(
    rows: Sequence[Mapping[str, Any]],
    kernel_index: Sequence[Mapping[str, Any]],
    store_index: Sequence[Mapping[str, Any]],
    candidate_rows: Sequence[Mapping[str, Any]] = (),
) -> List[str]:
    """Grade writer-authored definition rows against the ruling.

    A row whose id the kernel or Store already covers is a re-write (FAIL).
    Any other row is improvisation and must carry ``IMPROVISED_LABEL``, a
    non-empty source, and a candidate row naming it (FAIL otherwise).
    """
    candidates = {str(r.get("definition") or r.get("id") or "").strip() for r in candidate_rows or []}
    errs: List[str] = []
    for row in rows or []:
        ident = str(row.get("id") or row.get("key") or "").strip()
        if not ident:
            errs.append("definition row without an id")
            continue
        layer = resolve_layer(ident, kernel_index, store_index)["layer"]
        if layer == LAYER_KERNEL:
            errs.append(f"{ident}: kernel covers this; use the kernel definition")
            continue
        if layer == LAYER_STORE:
            errs.append(f"{ident}: the Store covers this; use the Store definition")
            continue
        if row.get("label") != IMPROVISED_LABEL:
            errs.append(f"{ident}: improvised without the label {IMPROVISED_LABEL!r}")
        source = row.get("source") or (row.get("provenance") or {}).get("reference")
        if not str(source or "").strip():
            errs.append(f"{ident}: improvised without a cited source")
        if ident not in candidates:
            errs.append(f"{ident}: improvised without a new_domain_candidates row")
    return errs



def _jsonl(path: Path) -> List[Dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows: List[Dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            rows.append({})  # an unreadable row is a row without an id
            continue
        rows.append(row if isinstance(row, dict) else {})
    return rows


def audit_product_formulas(
    product_root: Any,
    kernel_index: Sequence[Mapping[str, Any]],
    store_index: Sequence[Mapping[str, Any]],
) -> List[str]:
    """Grade what the writer authored in ``product_root`` and record it.

    Reads WRITER_DEFINITIONS_REL / WRITER_CANDIDATES_REL, runs
    :func:`writer_formula_errors`, and writes the findings to
    FORMULA_FINDINGS_REL (an empty list on a clean pass).
    """
    root = Path(product_root)
    errs = writer_formula_errors(
        _jsonl(root / WRITER_DEFINITIONS_REL),
        kernel_index,
        store_index,
        _jsonl(root / WRITER_CANDIDATES_REL),
    )
    target = root / FORMULA_FINDINGS_REL
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"errors": errs}, indent=2) + "\n", encoding="utf-8")
    return errs
