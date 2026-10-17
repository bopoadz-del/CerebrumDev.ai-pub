"""Names that belong to some product, loaded from data -- never typed here.

The Factory must not carry one product's identity into another: no capability
id, product id or name, or vertical from any build may appear in the Factory's
code or in another build's brief. This module assembles that set from where
those names actually live:

* the Store registry -- each block.json's declared ``verticals``, and each
  domain pack's ``domain_id`` / ``name``;
* every build on disk -- a session workspace's ``MANIFEST.json`` product id and
  its ``product-dna`` blueprint (product id, name, vertical, capability ids);
* the repo's golden blueprints;
* anything a caller passes in (CI adds the cerebrum-builds branch records).

Two structural rules keep it honest without a word list:

* Store block and kit ids are platform vocabulary -- the Factory references
  blocks by registry id -- so they are never treated as a product's identity.
* Only multi-word literals count (they contain ``_``, ``-`` or a space). A
  single word is vocabulary ("audit"), not identity ("appointment_scheduling").
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, FrozenSet, Iterable, Iterator, List, Optional, Set

try:  # PyYAML is a backend dependency; the module still loads without it.
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None  # type: ignore

_MULTI_WORD = re.compile(r"[_\- ]")
_IDENT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9 _\-]{3,79}$")


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _read_yaml(path: Path) -> Any:
    if yaml is None:
        return None
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 -- unreadable is absent
        return None


def _usable(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    if not text or not _IDENT_RE.match(text) or not _MULTI_WORD.search(text):
        return None
    return text


def _from_blueprint(doc: Any) -> Iterator[str]:
    if not isinstance(doc, dict):
        return
    body = doc.get("product") if isinstance(doc.get("product"), dict) else doc
    for key in ("product_id", "platform_id", "product_name", "name", "vertical", "id"):
        value = body.get(key) if isinstance(body, dict) else None
        if isinstance(value, str):
            yield value
    for cap in (body.get("capabilities") if isinstance(body, dict) else None) or []:
        if isinstance(cap, dict):
            for key in ("id", "capability_id", "name"):
                if isinstance(cap.get(key), str):
                    yield cap[key]
        elif isinstance(cap, str):
            yield cap


def store_block_ids(store_root: Optional[Path]) -> Set[str]:
    if not store_root:
        return set()
    reg = Path(store_root) / "block_registry"
    return {d.name for d in reg.iterdir() if d.is_dir()} if reg.is_dir() else set()


def from_store(store_root: Optional[Path]) -> Iterator[str]:
    if not store_root:
        return
    root = Path(store_root)
    reg = root / "block_registry"
    if reg.is_dir():
        for meta in reg.glob("*/block.json"):
            data = _read_json(meta)
            if isinstance(data, dict):
                for v in data.get("verticals") or []:
                    if isinstance(v, str):
                        yield v
    packs = root / "domain_packs"
    if packs.is_dir():
        for pack in packs.glob("*/pack.json"):
            data = _read_json(pack)
            manifest = data.get("manifest") if isinstance(data, dict) else None
            if isinstance(manifest, dict):
                for key in ("domain_id", "name"):
                    if isinstance(manifest.get(key), str):
                        yield manifest[key]


def from_build_dir(build: Path) -> Iterator[str]:
    """One build workspace: its manifest and its product-dna blueprint."""
    manifest = _read_json(build / "MANIFEST.json")
    if isinstance(manifest, dict):
        for key in ("product_id", "platform_id"):
            if isinstance(manifest.get(key), str):
                yield manifest[key]
    dna = build / "product-dna"
    yield from _from_blueprint(_read_yaml(dna / "product_blueprint.yaml"))
    resolution = _read_json(dna / "capability_resolution.json")
    if isinstance(resolution, dict):
        for key in resolution.get("capabilities") or resolution:
            if isinstance(key, str):
                yield key
            elif isinstance(key, dict) and isinstance(key.get("capability_id"), str):
                yield key["capability_id"]


def from_sessions(sessions_root: Optional[Path]) -> Iterator[str]:
    if not sessions_root or not Path(sessions_root).is_dir():
        return
    for manifest in Path(sessions_root).glob("*/*/MANIFEST.json"):
        yield from from_build_dir(manifest.parent)


def from_blueprints(blueprints_root: Optional[Path]) -> Iterator[str]:
    if not blueprints_root or not Path(blueprints_root).is_dir():
        return
    for path in Path(blueprints_root).rglob("*.yaml"):
        yield from _from_blueprint(_read_yaml(path))


def known_product_literals(
    *,
    store_root: Optional[Path] = None,
    sessions_root: Optional[Path] = None,
    blueprints_root: Optional[Path] = None,
    extra: Iterable[str] = (),
) -> FrozenSet[str]:
    """Every multi-word product identity the data knows, minus block ids."""
    blocks = store_block_ids(store_root)
    found: Set[str] = set()
    for source in (
        from_store(store_root),
        from_sessions(sessions_root),
        from_blueprints(blueprints_root),
        iter(extra),
    ):
        for raw in source:
            text = _usable(raw)
            if text and text not in blocks:
                found.add(text)
    return frozenset(found)


def default_roots() -> Dict[str, Optional[Path]]:
    """Where the data lives on this host (production or CI)."""
    store: Optional[Path] = None
    try:
        from app.factory.blocks_source import resolve_blocks_root

        store = resolve_blocks_root()
    except Exception:  # noqa: BLE001
        env = os.getenv("CEREBRUM_BLOCKS_ROOT")
        store = Path(env) if env else None
    sessions: Optional[Path] = None
    try:
        from app.factory.paths import factory_outputs_root

        sessions = factory_outputs_root() / "sessions"
    except Exception:  # noqa: BLE001
        sessions = None
    blueprints = Path(__file__).resolve().parents[4] / "blueprints"
    return {"store_root": store, "sessions_root": sessions, "blueprints_root": blueprints}


#: A machine identity: one token (no spaces) joined by underscores -- how the
#: Factory writes capability ids, product ids and platform ids
#: (``plt_<hex>``). A display name ("Vineyard Management Platform") has
#: spaces and is not an identity: two platforms may share one legitimately.
def is_identity_token(literal: str) -> bool:
    """True when ``literal`` is a machine identity, not a display name."""
    token = str(literal or "").strip()
    if not token or not token[0].isalpha():
        return False
    parts = token.split("_")
    return len(parts) >= 2 and all(p and p.isascii() and p.isalnum() for p in parts)


def foreign_identities_in(text: str, known: Iterable[str], own: Iterable[str]) -> List[str]:
    """Another platform's machine identities present in ``text``.

    The leakage evidence a BRIEF may be judged on. A brief is composed from
    this build's own blueprint, so an identity another platform declared --
    its capability id, product id or platform id -- and this blueprint did
    not, can only have come from another workspace. Display names are never
    evidence: they collide between platforms legitimately and appear in the
    user's own words. ``own`` is this build's declared identity (its product
    id, platform id, capability ids, block ids, vertical).
    """
    mine = {str(o).strip() for o in own if str(o).strip()}
    hits: List[str] = []
    blob = text or ""
    for literal in sorted(set(known)):
        token = str(literal).strip()
        if not is_identity_token(token) or token in mine:
            continue
        pattern = r"(?<![A-Za-z0-9_])" + re.escape(token) + r"(?![A-Za-z0-9_])"
        if re.search(pattern, blob):
            hits.append(token)
    return hits


def foreign_literals_in(text: str, known: Iterable[str], own: Iterable[str]) -> List[str]:
    """Known product literals present in ``text`` that are not this build's own."""
    mine = {str(o).strip().lower() for o in own if str(o).strip()}
    hits = []
    blob = text or ""
    for literal in sorted(set(known)):
        if literal.lower() in mine:
            continue
        pattern = r"(?<![A-Za-z0-9_])" + re.escape(literal) + r"(?![A-Za-z0-9_])"
        if re.search(pattern, blob, flags=re.IGNORECASE):
            hits.append(literal)
    return hits
