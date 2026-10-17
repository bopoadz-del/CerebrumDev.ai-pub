"""Stock kit packs into a generated product tree.

Floor runner exports vendored blocks under ``vendor/blocks/`` but never
copied the kit packs those blocks belong to (Factory shelf ``kit`` field,
resolved to Cerebrum-Blocks ``block_store/kits/`` -- the Factory holds no
kits of its own). The live winery-hospitality zip therefore had an
app and a vendor tree and no ``kits/`` — the customer called it rubbish.

Kits land at top-level ``kits/{kit_id}/`` (CLONER lane + ProductGenerator).
Blocks stay at ``vendor/blocks/`` — that is the product's block_registry.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

_FACTORY_DIR = Path(__file__).resolve().parent
_FACTORY_SHELF = _FACTORY_DIR / "shelves" / "factory_blocks.json"

_SKIP_DIR_NAMES = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".git",
    ".venv",
    "venv",
}
_SKIP_SUFFIXES = {".pyc", ".pyo"}


def kit_map_from_store(blocks_root: Optional[Path] = None) -> Dict[str, str]:
    """``block_id -> kit_id`` from the kits the Store marks available.

    Read from block_store/kits/<kit>/manifest.json, which states its own id,
    status and blocks. The Factory used to take this from its hand-written
    shelf, which assigned 20 of 25 blocks to "platform" and knew exactly one
    domain kit -- so a finance build was told no kit existed while
    kits/finance_ops sat there marked available, carrying a chart-of-accounts
    governance block the agent then wrote from scratch.
    """
    from app.factory.dual_registry import _default_blocks_root

    root = Path(blocks_root) if blocks_root else _default_blocks_root()
    kits_dir = root / "block_store" / "kits"
    out: Dict[str, str] = {}
    if not kits_dir.is_dir():
        return out
    for entry in sorted(kits_dir.iterdir()):
        manifest = entry / "manifest.json"
        if not entry.is_dir() or entry.name.startswith("_") or not manifest.is_file():
            continue
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        # The Store's own word on whether a kit may be offered.
        if str(data.get("status") or "").strip().lower() != "available":
            continue
        kit_id = str(data.get("id") or entry.name).strip()
        for block_id in data.get("blocks") or []:
            if block_id:
                out[str(block_id)] = kit_id
    return out


def load_shelf_kit_map(shelf_path: Optional[Path] = None) -> Dict[str, str]:
    """``block_id -> kit_id`` for every block the Factory may offer.

    The Store answers when it can; a block no kit claims is ``platform``.
    An explicit ``shelf_path`` is honoured as-is, and the bundled shelf is
    the fallback for an environment that cannot reach the Store.
    """
    # The shelf names each block's kit by registry id; the kit's FILES are
    # vendored from the Store (find_kit_source). What the Store holds is
    # reported by kit_map_from_store / store_catalog["store_kits"].
    if shelf_path is None:
        from app.factory.dual_registry import store_shelf_file

        shelf_path = store_shelf_file()
    path = shelf_path or _FACTORY_SHELF
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = json.loads(_FACTORY_SHELF.read_text(encoding="utf-8"))
    out: Dict[str, str] = {}
    for item in data.get("blocks", []):
        bid = item.get("id")
        if not bid:
            continue
        # The kit a block belongs to names a directory under
        # block_store/kits/, so find_kit_source vendors its files from the
        # Store. "platform" is the base every product stands on.
        out[str(bid)] = str(item.get("kit") or "platform")
    return out


def kits_for_blocks(
    block_ids: Iterable[str],
    *,
    shelf_path: Optional[Path] = None,
) -> Dict[str, List[str]]:
    """Group vendored block ids by the kit the Factory shelf assigns them.

    Unknown ids (tests, GENERATE-only faux blocks) still land in ``platform``
    so a product that has blocks always has a kit pack.
    """
    shelf = load_shelf_kit_map(shelf_path)
    grouped: Dict[str, List[str]] = {}
    for bid in block_ids:
        kit_id = shelf.get(bid, "platform")
        grouped.setdefault(kit_id, []).append(bid)
    for kit_id in grouped:
        grouped[kit_id] = sorted(set(grouped[kit_id]))
    return dict(sorted(grouped.items()))


def _store_root(blocks_root: Optional[Path]) -> Optional[Path]:
    if blocks_root:
        return Path(blocks_root)
    try:
        from app.factory.blocks_source import resolve_blocks_root

        return Path(resolve_blocks_root())
    except Exception:  # noqa: BLE001 -- no Store: no kit, synthesized pack
        return None


def store_kit_dir(kit_id: str, blocks_root: Optional[Path] = None) -> Optional[Path]:
    """The Store's directory for a kit, by registry id; None when absent."""
    root = _store_root(blocks_root)
    if root is None or not kit_id or "/" in kit_id or "\\" in kit_id:
        return None
    path = root / "block_store" / "kits" / kit_id
    return path if path.is_dir() else None


def _kit_source_candidates(
    kit_id: str, blocks_root: Optional[Path]
) -> List[Path]:
    # The Factory holds no kits (owner rule): every kit is the Store's.
    candidates: List[Path] = []
    root = _store_root(blocks_root)
    if root is not None:
        candidates.extend(
            [
                root / "block_store" / "kits" / kit_id,
                root / "kits" / kit_id,
            ]
        )
    return candidates


def find_kit_source(kit_id: str, blocks_root: Optional[Path] = None) -> Optional[Path]:
    """Richest on-disk kit pack for ``kit_id``, or None to synthesize."""
    found: List[Path] = []
    for candidate in _kit_source_candidates(kit_id, blocks_root):
        if candidate.is_dir() and any(candidate.iterdir()):
            found.append(candidate)
    if not found:
        return None

    def _score(path: Path) -> tuple:
        file_count = sum(1 for p in path.rglob("*") if p.is_file())
        has_manifest = (path / "manifest.json").is_file()
        return (file_count, has_manifest)

    return max(found, key=_score)


def find_kit_manifest(kit_id: str, blocks_root: Optional[Path] = None) -> Optional[Path]:
    for candidate in _kit_source_candidates(kit_id, blocks_root):
        manifest = candidate / "manifest.json"
        if manifest.is_file():
            return manifest
    return None


def iter_kit_files(source: Path) -> List[Path]:
    files: List[Path] = []
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        if any(part in _SKIP_DIR_NAMES for part in path.relative_to(source).parts):
            continue
        if path.suffix in _SKIP_SUFFIXES:
            continue
        files.append(path)
    return files


#: What a brief carries of a kit manifest: identity and the block contract.
#: A kit manifest also records where the kit came from (the product it was
#: carved out of, that product's capabilities and blueprint); that is the
#: kit's history, not a build's work, and never reaches a coder. The brief
#: compiler projects onto these keys and the brief lint refuses any other.
KIT_BRIEF_IDENTITY_KEYS = ("id", "name", "version", "source", "product_blocks", "vendored_blocks")
KIT_BRIEF_CONTRACT_KEYS = ("blocks", "inputs", "fields", "reads", "writes")


def kit_brief_view(manifest: Mapping[str, Any], claimed: Sequence[str]) -> Dict[str, Any]:
    """The manifest restricted to identity + the contract of ``claimed`` blocks."""
    keep = set(claimed)

    def _restrict(value: Any) -> Any:
        if isinstance(value, list):
            return [
                v for v in value
                if (v.get("id") or v.get("name") if isinstance(v, dict) else v) in keep
            ]
        if isinstance(value, dict):
            out = {}
            for k, v in value.items():
                if k in keep:
                    out[k] = v
                elif isinstance(v, (list, dict)):
                    sub = _restrict(v)
                    if sub:
                        out[k] = sub
            return out
        return value

    view: Dict[str, Any] = {k: manifest[k] for k in KIT_BRIEF_IDENTITY_KEYS if k in manifest}
    for key in KIT_BRIEF_CONTRACT_KEYS:
        if key in manifest:
            view[key] = _restrict(manifest[key])
    return view


def render_kit_manifest(
    kit_id: str,
    block_ids: Sequence[str],
    *,
    source_kind: str,
    existing: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Kit pack manifest. Preserve an upstream manifest and pin vendored paths."""
    body: Dict[str, Any] = dict(existing) if existing else {}
    body.setdefault("id", kit_id)
    body.setdefault("name", kit_id.replace("_", " ").title())
    body.setdefault("version", "1.0.0")
    body["source"] = source_kind
    # Always record the blocks this product actually vendored, even when the
    # upstream manifest lists a wider kit (estate kit includes platform compose).
    body["product_blocks"] = list(block_ids)
    body["vendored_blocks"] = {bid: f"vendor/blocks/{bid}" for bid in block_ids}
    if "blocks" not in body:
        body["blocks"] = list(block_ids)
    return body


def stock_kits(
    dest_root: Path,
    block_ids: Sequence[str],
    *,
    blocks_root: Optional[Path] = None,
    shelf_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Write ``kits/{kit_id}/`` under ``dest_root``. Returns lock-file records."""
    grouped = kits_for_blocks(block_ids, shelf_path=shelf_path)
    records: Dict[str, Any] = {}
    for kit_id, bids in grouped.items():
        records[kit_id] = _stock_one_kit(
            dest_root / "kits" / kit_id,
            kit_id,
            bids,
            blocks_root=blocks_root,
        )
    return records


def _stock_one_kit(
    dest: Path,
    kit_id: str,
    block_ids: Sequence[str],
    *,
    blocks_root: Optional[Path],
) -> Dict[str, Any]:
    source = find_kit_source(kit_id, blocks_root)
    source_kind = "synthesized-from-factory-shelf"
    existing_manifest: Optional[Dict[str, Any]] = None
    files_copied = 0

    if source is not None:
        source_kind = _classify_source(source)
        dest.mkdir(parents=True, exist_ok=True)
        for src_file in iter_kit_files(source):
            rel = src_file.relative_to(source)
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_file, target)
            files_copied += 1
        manifest_path = dest / "manifest.json"
        if manifest_path.is_file():
            try:
                loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    existing_manifest = loaded
            except json.JSONDecodeError:
                existing_manifest = None
    else:
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "README.md").write_text(
            f"# {kit_id}\n\n"
            "Kit pack synthesized from the Factory dual-register shelf "
            "(`backend/app/factory/shelves/factory_blocks.json`) because no "
            "on-disk kit tree was available for this id. Blocks used by this "
            "product are vendored at `vendor/blocks/`.\n",
            encoding="utf-8",
        )

    manifest_src = find_kit_manifest(kit_id, blocks_root)
    if existing_manifest is None and manifest_src is not None:
        try:
            loaded = json.loads(manifest_src.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                existing_manifest = loaded
                source_kind = _classify_source(manifest_src.parent)
        except json.JSONDecodeError:
            existing_manifest = None

    manifest = render_kit_manifest(
        kit_id,
        block_ids,
        source_kind=source_kind,
        existing=existing_manifest,
    )
    (dest / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "path": f"kits/{kit_id}",
        "source": source_kind,
        "blocks": list(block_ids),
        "files_copied": files_copied,
    }


def _classify_source(path: Path) -> str:
    resolved = path.resolve()
    if "block_store" in resolved.parts:
        return "cerebrum-blocks"
    return "kit-source"


def stock_kits_via_workspace(
    workspace: Any,
    block_ids: Sequence[str],
    *,
    blocks_root: Optional[Path] = None,
    shelf_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """CLONER path: same kit packs, written through a lane-checked workspace."""
    grouped = kits_for_blocks(block_ids, shelf_path=shelf_path)
    records: Dict[str, Any] = {}
    for kit_id, bids in grouped.items():
        records[kit_id] = _stock_one_kit_workspace(
            workspace, kit_id, bids, blocks_root=blocks_root
        )
    return records


def _stock_one_kit_workspace(
    workspace: Any,
    kit_id: str,
    block_ids: Sequence[str],
    *,
    blocks_root: Optional[Path],
) -> Dict[str, Any]:
    source = find_kit_source(kit_id, blocks_root)
    source_kind = "synthesized-from-factory-shelf"
    existing_manifest: Optional[Dict[str, Any]] = None
    files_copied = 0
    dest_rel = Path("kits") / kit_id

    if source is not None:
        source_kind = _classify_source(source)
        for src_file in iter_kit_files(source):
            rel = src_file.relative_to(source)
            workspace.copy_file(src_file, dest_rel / rel)
            files_copied += 1
        try:
            raw = workspace.read_text(dest_rel / "manifest.json")
            loaded = json.loads(raw)
            if isinstance(loaded, dict):
                existing_manifest = loaded
        except (OSError, json.JSONDecodeError, FileNotFoundError):
            existing_manifest = None

    manifest_src = find_kit_manifest(kit_id, blocks_root)
    if existing_manifest is None and manifest_src is not None:
        try:
            loaded = json.loads(manifest_src.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                existing_manifest = loaded
                source_kind = _classify_source(manifest_src.parent)
        except json.JSONDecodeError:
            existing_manifest = None

    if source is None:
        workspace.write_text(
            dest_rel / "README.md",
            f"# {kit_id}\n\n"
            "Kit pack synthesized from the Factory dual-register shelf "
            "(`backend/app/factory/shelves/factory_blocks.json`) because no "
            "on-disk kit tree was available for this id. Blocks used by this "
            "product are vendored at `vendor/blocks/`.\n",
        )

    manifest = render_kit_manifest(
        kit_id,
        block_ids,
        source_kind=source_kind,
        existing=existing_manifest,
    )
    workspace.write_text(
        dest_rel / "manifest.json",
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
    )
    return {
        "path": f"kits/{kit_id}",
        "source": source_kind,
        "blocks": list(block_ids),
        "files_copied": files_copied,
    }
