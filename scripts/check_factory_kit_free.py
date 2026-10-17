#!/usr/bin/env python3
"""The Factory holds no kits (owner rule). Fail if it holds one again.

A kit is recognised by SHAPE, never by name:

* any tracked path with a ``kits`` directory under ``backend/``;
* any tracked ``manifest.json`` under ``backend/`` that is kit-shaped (an
  ``id`` plus a ``blocks`` declaration);
* any tracked ``*_runtime`` directory under ``backend/app`` (a vertical's
  runtime shipped inside the Factory);
* any tracked path under ``backend/app`` that holds its own ``app/``
  package (a product tree embedded in the Factory, e.g. an overlay).

Kits live in the Store (Cerebrum-Blocks ``block_store/kits/``) and the
Factory references them by registry id. Exit 1 lists file paths.

    python scripts/check_factory_kit_free.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Iterable, List

ROOT = Path(__file__).resolve().parent.parent


def tracked(root: Path = ROOT) -> List[str]:
    out = subprocess.run(
        ["git", "ls-files", "backend"], cwd=root, capture_output=True, text=True, check=True
    ).stdout
    return [line for line in out.splitlines() if line]


def violations(paths: Iterable[str], root: Path = ROOT) -> List[str]:
    found: List[str] = []
    for rel in paths:
        parts = Path(rel).parts
        if "kits" in parts[:-1]:
            found.append(f"{rel}: inside a kits/ directory")
            continue
        if parts[:2] == ("backend", "app") and any(p.endswith("_runtime") for p in parts[:-1]):
            found.append(f"{rel}: inside a *_runtime directory")
            continue
        if parts[:2] == ("backend", "app") and "app" in parts[2:-1]:
            found.append(f"{rel}: inside a product tree embedded under backend/app")
            continue
        if parts[-1] == "manifest.json":
            try:
                data = json.loads((root / rel).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict) and "id" in data and "blocks" in data:
                found.append(f"{rel}: kit-shaped manifest (id + blocks)")
    return found


def main() -> int:
    found = violations(tracked())
    if found:
        print("REJECTED: the Factory holds no kits -- move it to the Store:", file=sys.stderr)
        for line in found:
            print("  " + line, file=sys.stderr)
        return 1
    print("factory kit-free: no kits/, no kit manifest, no *_runtime directory")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
