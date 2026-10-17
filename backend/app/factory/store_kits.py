"""What the Store's kits declare about themselves, read at build time.

The Factory holds no kit facts. Which kit serves a vertical, whether the
operator declared it ready to build against, and which of its blocks are
domain content are fields of the kit's own manifest in the Store:

* domain kits   ``block_store/kits/<kit>/manifest.json``  -- ``serves_verticals``,
  ``build_ready``, ``blocks``;
* reasoning kits ``app/blocks/<kit>/manifest.yaml`` (+ ``invariants.yaml``)
  -- ``serves_verticals``.

A vertical is served by the kit whose id IS the vertical or whose
``serves_verticals`` lists it. Nothing here names a kit or a vertical.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional, Tuple


def slug(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _root(store_root: Any) -> Optional[Path]:
    if store_root is not None:
        return Path(store_root)
    try:
        from app.factory.blocks_source import resolve_blocks_root

        return Path(resolve_blocks_root())
    except Exception:  # noqa: BLE001 -- no Store means no kit facts, not a crash
        return None


@lru_cache(maxsize=8)
def _domain_kits(root: str) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for path in sorted((Path(root) / "block_store" / "kits").glob("*/manifest.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue  # a template, not a kit
        if isinstance(data, dict):
            out[str(data.get("id") or path.parent.name)] = data
    return out


@lru_cache(maxsize=8)
def _reasoning_kits(root: str) -> Dict[str, Dict[str, Any]]:
    import yaml

    # A kit is its two declarative files; half a kit is not a kit (the build
    # names the missing file -- see reasoning_socket.emit).
    out: Dict[str, Dict[str, Any]] = {}
    for path in sorted((Path(root) / "app" / "blocks").glob("*/manifest.yaml")):
        if not (path.parent / "invariants.yaml").is_file():
            continue
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            continue
        if isinstance(data, dict):
            out[path.parent.name] = data
    return out


def domain_kits(store_root: Any = None) -> Dict[str, Dict[str, Any]]:
    root = _root(store_root)
    return _domain_kits(str(root)) if root else {}


def reasoning_kits(store_root: Any = None) -> Dict[str, Dict[str, Any]]:
    root = _root(store_root)
    return _reasoning_kits(str(root)) if root else {}


def serving_kit(vertical: Any, kits: Dict[str, Dict[str, Any]]) -> Optional[str]:
    """The kit that declares it serves ``vertical`` (or is named for it)."""
    want = slug(vertical)
    if not want:
        return None
    if want in kits:
        return want
    for kit_id, manifest in kits.items():
        if want in {slug(v) for v in (manifest.get("serves_verticals") or [])}:
            return kit_id
    return None


#: How a vertical resolves against the Store's kits.
SERVED = "SERVED"
UNSUPPORTED = "UNSUPPORTED"
UNKNOWN = "UNKNOWN"


def excluding_kit(vertical: Any, kits: Dict[str, Dict[str, Any]]) -> Optional[str]:
    """The kit that declares this vertical OUT of its coverage
    (``excludes_verticals``), if any."""
    want = slug(vertical)
    if not want:
        return None
    for kit_id, manifest in sorted(kits.items()):
        if want in {slug(v) for v in (manifest.get("excludes_verticals") or [])}:
            return kit_id
    return None


def resolve_vertical(
    vertical: Any, kits: Dict[str, Dict[str, Any]]
) -> Tuple[str, Optional[str], str]:
    """(status, kit, reason). A kit's exclusion wins over any claim to serve:
    the Store said this vertical must not be built on it."""
    excluded_by = excluding_kit(vertical, kits)
    if excluded_by:
        return UNSUPPORTED, excluded_by, f"excluded by Store kit {excluded_by}"
    kit = serving_kit(vertical, kits)
    if kit:
        return SERVED, kit, f"served by Store kit {kit}"
    return UNKNOWN, None, "no Store kit serves or excludes this vertical"


def domain_blocks(kit_id: str, kits: Dict[str, Dict[str, Any]]) -> FrozenSet[str]:
    """A kit's own domain blocks: its blocks minus the shared core.

    The shared core is whatever most of the Store's domain kits carry (pdf,
    ocr, chat ... today) -- derived from the manifests, never listed.
    """
    lists = [m.get("blocks") for m in kits.values() if isinstance(m.get("blocks"), list)]
    if not lists:
        return frozenset()
    counts: Dict[str, int] = {}
    for blocks in lists:
        for b in set(blocks):
            counts[b] = counts.get(b, 0) + 1
    shared = {b for b, n in counts.items() if n * 2 > len(lists)}
    own = kits.get(kit_id, {}).get("blocks")
    return frozenset(b for b in own if b not in shared) if isinstance(own, list) else frozenset()


@lru_cache(maxsize=8)
def _block_reads(root: str) -> Dict[str, Tuple[Tuple[str, str], ...]]:
    """block id -> the (kind, scope) reads its signed ``block.json`` declares."""
    out: Dict[str, Tuple[Tuple[str, str], ...]] = {}
    for path in sorted((Path(root) / "block_registry").glob("*/block.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        reads = data.get("reads") if isinstance(data, dict) else None
        if not isinstance(reads, list):
            continue
        out[str(data.get("id") or path.parent.name)] = tuple(
            (str(r.get("kind") or ""), str(r.get("scope") or ""))
            for r in reads
            if isinstance(r, dict)
        )
    return out


def blocks_declaring_read(kind: str, scope: str, store_root: Any = None) -> FrozenSet[str]:
    """The Store blocks whose ``block.json`` declares a read of ``kind`` /
    ``scope`` -- what a block says it consumes, from the Store's own signed
    manifests. No block is named here."""
    root = _root(store_root)
    if not root:
        return frozenset()
    return frozenset(
        bid for bid, reads in _block_reads(str(root)).items() if (kind, scope) in reads
    )


#: The ``capability_class`` words the Factory dispatches on, from the Store's
#: ``manifest_contract.CAPABILITY_CLASSES``. None is a block id: a site that
#: compares against one of these is reading a declaration.
MESSAGING = "messaging"
ORCHESTRATION = "orchestration"
MEMBERSHIP = "membership"
DOCUMENT_EXTRACTION = "document_extraction"
METRICS = "metrics"
EVENTS = "events"
RECORDS = "records"
JOBS = "jobs"
LAYOUT = "layout"
GOVERNANCE = "governance"
VISION_CAPTURE = "vision_capture"
SHARED_REASONING = "shared_reasoning"
CONVERSATION = "conversation"


@lru_cache(maxsize=8)
def _block_manifests(root: str) -> Dict[str, Dict[str, Any]]:
    """block id -> its signed ``block.json``, as the Store holds it."""
    out: Dict[str, Dict[str, Any]] = {}
    for path in sorted((Path(root) / "block_registry").glob("*/block.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            out[str(data.get("id") or path.parent.name)] = data
    return out


def block_manifests(store_root: Any = None) -> Dict[str, Dict[str, Any]]:
    root = _root(store_root)
    return _block_manifests(str(root)) if root else {}


def block_manifest(block_id: Any, store_root: Any = None) -> Dict[str, Any]:
    return block_manifests(store_root).get(str(block_id or "").strip()) or {}


def capability_class(manifest: Any) -> Optional[str]:
    """The ``capability_class`` a manifest declares, or None."""
    value = manifest.get("capability_class") if isinstance(manifest, dict) else None
    return value.strip() if isinstance(value, str) and value.strip() else None


def block_capability_class(block_id: Any, store_root: Any = None) -> Optional[str]:
    """What kind of capability the block declares it IS. Every site that
    must treat one kind of block differently reads this, never the id."""
    return capability_class(block_manifest(block_id, store_root))


def blocks_of_class(cls: str, store_root: Any = None) -> FrozenSet[str]:
    """Every Store block that declares ``capability_class == cls``."""
    return frozenset(
        bid for bid, m in block_manifests(store_root).items() if capability_class(m) == cls
    )


def capability_classes(block_ids: Any, store_root: Any = None) -> Dict[str, str]:
    """block id -> declared class, for the ids that declare one."""
    out: Dict[str, str] = {}
    for raw in block_ids or ():
        cls = block_capability_class(raw, store_root)
        if cls:
            out[str(raw)] = cls
    return out


def not_attachable(store_root: Any = None) -> Dict[str, str]:
    """block id -> reason, for every block whose manifest declares
    ``factory_attach.cleared`` false."""
    out: Dict[str, str] = {}
    for bid, manifest in block_manifests(store_root).items():
        attach = manifest.get("factory_attach")
        if isinstance(attach, dict) and attach.get("cleared") is False:
            out[bid] = str(attach.get("reason") or "declared not cleared")
    return out


#: The vertical a product has when the user chose none. The Factory never
#: infers one -- not from the brief's prose, not from the blocks it binds.
NO_VERTICAL = "product"


def chosen_vertical(value: Any) -> str:
    """The user's vertical choice, normalised; ``NO_VERTICAL`` when absent.

    The ONLY way a product gets a vertical: the user picks it on the Floor
    (or types it). Every kit lookup downstream reads this value.
    """
    s = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in slug(value))
    s = "_".join(part for part in s.split("_") if part)[:48]
    return s or NO_VERTICAL


def declared_verticals(store_root: Any = None) -> List[Dict[str, Any]]:
    """The verticals the Store's domain kits DECLARE they serve, for the
    Floor's picker: ``[{"vertical", "kit", "build_ready"}]``. Read from the
    kits' own manifests; the Factory adds and removes nothing."""
    out: List[Dict[str, Any]] = []
    for kit_id, manifest in sorted(domain_kits(store_root).items()):
        for vertical in manifest.get("serves_verticals") or []:
            v = slug(vertical)
            if v:
                out.append({"vertical": v, "kit": kit_id,
                            "build_ready": manifest.get("build_ready") is True})
    return sorted(out, key=lambda row: (row["vertical"], row["kit"]))
