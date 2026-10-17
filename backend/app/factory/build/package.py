"""Immutable identified package for a RoleRunner tree.

S8: two identical inputs must produce the same content digest. Residue
(ledger, caches, staging) is named and excluded. The identity file is
written last and is not hashed into itself.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

IDENTITY_REL = Path("docs") / "package_identity.json"

RESIDUE_NAMES = frozenset(
    {
        "build_ledger.jsonl",
        "__pycache__",
        ".pytest_cache",
        ".factory-staging",
    }
)
RESIDUE_SUFFIXES = (".pyc", ".pyo")


def is_residue(rel: str) -> bool:
    parts = Path(rel).parts
    if any(part in RESIDUE_NAMES for part in parts):
        return True
    return rel.endswith(RESIDUE_SUFFIXES)


def residue_paths(root: Path) -> Tuple[str, ...]:
    base = Path(root)
    found: List[str] = []
    if not base.is_dir():
        return ()
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(base).as_posix()
        if is_residue(rel):
            found.append(rel)
    return tuple(found)


def iter_payload_files(root: Path) -> Iterable[Path]:
    base = Path(root)
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(base).as_posix()
        if is_residue(rel):
            continue
        if Path(rel) == IDENTITY_REL:
            continue
        yield path


def artifact_digest(root: Path) -> str:
    """SHA-256 of path + bytes for every non-residue, non-identity file."""
    base = Path(root)
    h = hashlib.sha256()
    for path in iter_payload_files(base):
        rel = path.relative_to(base).as_posix()
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def identity_document(root: Path, *, extra: Dict[str, Any] | None = None) -> Dict[str, Any]:
    digest = artifact_digest(root)
    doc: Dict[str, Any] = {
        "schema_version": "package_identity.v1",
        "algorithm": "sha256",
        "digest": digest,
        "digest_excludes": sorted(RESIDUE_NAMES) + [IDENTITY_REL.as_posix()],
        "residue_present": list(residue_paths(root)),
    }
    if extra:
        doc.update(extra)
    return doc


def write_identity(workspace: Any, *, extra: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Stamp identity into a RoleWorkspace after every other write."""
    dest = Path(getattr(workspace, "workspace", workspace))
    doc = identity_document(dest, extra=extra)
    workspace.write_text(
        IDENTITY_REL, json.dumps(doc, indent=2, sort_keys=True) + "\n"
    )
    return doc
