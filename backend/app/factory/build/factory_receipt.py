"""The Factory's receipt: which files and dependencies the FACTORY put in a build.

A Store-gate audit finding (bandit, pip-audit) is owned by whoever wrote the
line it points at. Two authors write a platform: the Factory (stamped files,
vendored Store code, the base requirements it computes) and the writer
(everything else). This receipt is the Factory's side of that record, written
at the moment the workspace is handed to the gate -- after the last restamp --
so ownership is read from provenance, never guessed from a filename:

* ``files``: every Factory-rendered file present, with the sha256 of the text
  the Factory left there. A finding in a file whose bytes still match is the
  Factory's; a Factory file whose bytes changed is of unknown origin (the
  writer may not edit those lanes), so it is the Factory's too -- fail-safe.
* ``vendored``: the paths CLONER vendored from the Store (blocks.lock.json).
* ``base_requirements``: every distribution the Factory declared for this
  tree -- its requirements rendering (base lines, vendored-block imports,
  framework extras) plus every line in requirements.txt's Factory block as
  the stamp left it. A dependency outside it that the build's
  requirements.txt declares is one the writer added; one the file does not
  declare (a transitive pin, e.g. of a Factory-declared block dependency) is
  never the writer's.

Everything in the workspace that is not on the receipt was written by the
writer. A build with no receipt (built before this existed) cannot be
attributed, and an unattributable finding is a Factory fault -- never silently
the writer's.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Dict, FrozenSet, Iterable, Mapping, Optional, Sequence, Tuple

RECEIPT_REL = "docs/factory_receipt.json"
SCHEMA = "factory_receipt.v1"

#: Owner words, the same vocabulary the floor's owner derivation uses.
WRITER = "WRITER"
FACTORY = "FACTORY"

REASON_NO_RECEIPT = "no factory receipt for this build: origin unknown, a Factory fault"
REASON_FACTORY_FILE = "in a file the Factory rendered"
REASON_FACTORY_FILE_CHANGED = (
    "in a Factory-rendered file whose bytes differ from the Factory's stamp: "
    "origin unknown, a Factory fault"
)
REASON_VENDORED = "in Store code the Factory vendored"
REASON_NOT_IN_TREE = "names a file that is not in the build: origin unknown, a Factory fault"
REASON_FACTORY_DEPENDENCY = "a dependency the Factory declared in requirements.txt"
REASON_UNDECLARED_DEPENDENCY = (
    "a dependency the build's requirements.txt does not declare (transitive or "
    "unknown): origin unknown, a Factory fault"
)
REASON_WRITER_FILE = "in a file the writer authored"
REASON_WRITER_DEPENDENCY = "a dependency the writer added to requirements.txt"


def _norm_rel(path: Any) -> str:
    parts = [p for p in PurePosixPath(str(path or "").replace("\\", "/")).parts if p not in ("/", ".")]
    return "/".join(parts)


def _digest(path: Path) -> str:
    text = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(text).hexdigest()


def _dist(line: str) -> str:
    from app.factory.build.factory_refresh import _dist as dist

    return str(dist(line) or "")


def _requirement_dists(text: str) -> FrozenSet[str]:
    return frozenset(d for d in (_dist(line) for line in str(text or "").splitlines()) if d)


def _vendored_paths(root: Path) -> Tuple[str, ...]:
    """What CLONER vendored, from its own lock (block dirs + runtime files)."""
    lock_path = root / "blocks.lock.json"
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    out = []
    for entry in (lock.get("blocks") or {}).values():
        if isinstance(entry, Mapping) and entry.get("path"):
            out.append(_norm_rel(entry["path"]))
    runtime = lock.get("runtime") or {}
    if isinstance(runtime, Mapping):
        if runtime.get("path"):
            out.append(_norm_rel(runtime["path"]))
        out.extend(_norm_rel(f) for f in runtime.get("files") or [])
    return tuple(dict.fromkeys(p for p in out if p))


def base_requirement_dists(root: Path) -> FrozenSet[str]:
    """The distributions the Factory's own requirements rendering declares."""
    from app.factory.build.block_obligations import dependency_obligations_on_disk
    from app.factory.build.roles_handlers import _render_requirements

    return _requirement_dists(
        _render_requirements(dependency_obligations_on_disk(root), root=root)
    )


def factory_block_dists(root: Path) -> FrozenSet[str]:
    """The distributions in requirements.txt's Factory block: what the stamp
    wrote there, whether or not today's render would still list them."""
    from app.factory.build.factory_block import split_block

    req = root / "requirements.txt"
    if not req.is_file():
        return frozenset()
    _before, body, _after = split_block(req.read_bytes().decode("utf-8"))
    return _requirement_dists(body or "")


def factory_stamped_paths() -> Tuple[str, ...]:
    """Every file the Factory writes into a build and the writer may not own.

    The harness files (factory_rendered_paths) AND every path a Factory stamp
    owns outright -- read from the ONE stamp registry (stamp_registry): the
    deploy modules (a crash inside the Factory's own app/observe.py is the
    Factory's), the self-check, TESTER's suites. A shared file's product
    bytes are never claimed here: only its Factory block is the Factory's.
    """
    from app.factory.build.stamp_registry import owned_paths
    from app.factory.build.store_acceptance import factory_rendered_paths

    seen: Dict[str, None] = {}
    for rel in (*factory_rendered_paths(), *owned_paths()):
        rel = _norm_rel(rel)
        if rel:
            seen.setdefault(rel, None)
    return tuple(seen)


def record_receipt(root: Path | str) -> Dict[str, Any]:
    """Write the receipt for the workspace as it is now; return it."""
    root = Path(root)
    files: Dict[str, str] = {}
    for rel in factory_stamped_paths():
        rel = _norm_rel(rel)
        path = root / rel
        if rel and path.is_file():
            files[rel] = _digest(path)
    receipt = {
        "schema": SCHEMA,
        "files": dict(sorted(files.items())),
        "vendored": list(_vendored_paths(root)),
        "base_requirements": sorted(base_requirement_dists(root) | factory_block_dists(root)),
    }
    out = root / RECEIPT_REL
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt


@dataclass(frozen=True)
class Origin:
    owner: str
    reason: str


@dataclass(frozen=True)
class Receipt:
    root: Path
    files: Mapping[str, str]
    vendored: Tuple[str, ...]
    base_requirements: FrozenSet[str]

    def _vendored(self, rel: str) -> bool:
        return any(rel == v or rel.startswith(v.rstrip("/") + "/") for v in self.vendored)

    def file_origin(self, path: Any) -> Origin:
        """Who wrote the file a finding points at."""
        rel = _norm_rel(path)
        if rel in self.files:
            disk = self.root / rel
            if disk.is_file() and _digest(disk) == self.files[rel]:
                return Origin(FACTORY, REASON_FACTORY_FILE)
            return Origin(FACTORY, REASON_FACTORY_FILE_CHANGED)
        if self._vendored(rel):
            return Origin(FACTORY, REASON_VENDORED)
        if rel and (self.root / rel).is_file():
            return Origin(WRITER, REASON_WRITER_FILE)
        return Origin(FACTORY, REASON_NOT_IN_TREE)

    def dependency_origin(self, package: Any) -> Origin:
        """Who brought in the distribution a vulnerability is in."""
        dist = _dist(str(package or ""))
        if dist in self.base_requirements:
            return Origin(FACTORY, REASON_FACTORY_DEPENDENCY)
        req = self.root / "requirements.txt"
        declared = _requirement_dists(req.read_text(encoding="utf-8")) if req.is_file() else frozenset()
        if dist and dist in declared:
            return Origin(WRITER, REASON_WRITER_DEPENDENCY)
        return Origin(FACTORY, REASON_UNDECLARED_DEPENDENCY)


def load_receipt(root: Path | str) -> Optional[Receipt]:
    root = Path(root)
    try:
        data = json.loads((root / RECEIPT_REL).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, Mapping) or data.get("schema") != SCHEMA:
        return None
    return Receipt(
        root=root,
        files={_norm_rel(k): str(v) for k, v in (data.get("files") or {}).items()},
        vendored=tuple(_norm_rel(v) for v in data.get("vendored") or []),
        base_requirements=frozenset(str(d) for d in data.get("base_requirements") or []),
    )


#: Typed evidence row kinds the Store gate emits (cerebrum-builds
#: .github/store_gate/audit_evidence.py). A row is a mapping, never parsed text.
ROW_FILE = "file"  # bandit: {"kind": "file", "file", "line", "test_id", "severity", "text"}
ROW_DEPENDENCY = "dependency"  # pip-audit: {"kind": "dependency", "package", "version", "vuln_id"}


def row_origin(receipt: Optional[Receipt], row: Mapping[str, Any]) -> Origin:
    if receipt is None:
        return Origin(FACTORY, REASON_NO_RECEIPT)
    if row.get("kind") == ROW_DEPENDENCY:
        return receipt.dependency_origin(row.get("package"))
    return receipt.file_origin(row.get("file"))


def row_text(row: Mapping[str, Any]) -> str:
    """The human line for a typed row (display only; never parsed back)."""
    if row.get("kind") == ROW_DEPENDENCY:
        return f"{row.get('vuln_id', '?')} {row.get('package', '?')}=={row.get('version', '?')}"
    return (
        f"{row.get('test_id', '?')} {row.get('file', '?')}:{row.get('line', '?')} "
        f"[{str(row.get('severity') or '').upper()}] {str(row.get('text') or '').strip()}"
    )


def split_rows(
    receipt: Optional[Receipt], rows: Iterable[Mapping[str, Any]]
) -> Tuple[Sequence[Mapping[str, Any]], Sequence[Tuple[Mapping[str, Any], str]]]:
    """(writer rows, [(factory row, reason)]) -- every row lands in exactly one."""
    writer, factory = [], []
    for row in rows or ():
        if not isinstance(row, Mapping):
            continue
        origin = row_origin(receipt, row)
        if origin.owner == WRITER:
            writer.append(row)
        else:
            factory.append((row, origin.reason))
    return writer, factory
