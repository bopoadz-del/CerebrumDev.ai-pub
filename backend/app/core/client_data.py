"""The client data a generated package carries, as its packager declared it.

A packager writes the client's documents, vectors and environment into the
package; at the moment it writes each one it declares that path here. The
declaration is a manifest inside the package, so whoever later ships the
package (the deployer's private-repo guard) reads what was actually written
instead of keeping its own list of names to look for.
"""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import List, Optional, Sequence

#: The manifest file, at the package root.
MANIFEST_NAME = "client_data.json"


def declare(package_root: Path, *written: Path) -> None:
    """Record ``written`` (paths inside ``package_root``) as client data."""
    root = Path(package_root)
    manifest = root / MANIFEST_NAME
    paths = set(declared(root) or [])
    for path in written:
        paths.add(PurePosixPath(Path(path).relative_to(root).as_posix()))
    manifest.write_text(
        json.dumps({"client_data": sorted(str(p) for p in paths)}, indent=2),
        encoding="utf-8",
    )


def declared(package_dir: Path | str) -> Optional[List[PurePosixPath]]:
    """The client-data paths a package declares, or None without a manifest."""
    manifest = Path(package_dir) / MANIFEST_NAME
    if not manifest.is_file():
        return None
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    return [PurePosixPath(p) for p in payload.get("client_data") or []]


def covers(declared_paths: Sequence[PurePosixPath], path: PurePosixPath) -> bool:
    """True when ``path`` is a declared path or lies inside a declared one.

    Compared as path components, never as substrings.
    """
    return any(path == d or d in path.parents for d in declared_paths)
