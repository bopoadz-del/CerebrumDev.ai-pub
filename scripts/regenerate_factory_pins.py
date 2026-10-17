#!/usr/bin/env python3
"""Regenerate backend/app/factory/build/factory_pins.lock.json.

The lock is the ONE pin set every Factory-built product installs against.
Change a top-level version in the lock's ``top_level_versions``, then run
this (it needs the package index) to re-resolve the closed set for the
product image's platform (python:3.12-slim, glibc manylinux) and record what
every resolved wheel declares. Never hand-edit a single entry: the closure
test (tests/factory/test_factory_dependency_pins.py) fails on a set that is
not closed.

    python scripts/regenerate_factory_pins.py
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

LOCK = Path(__file__).resolve().parents[1] / "backend" / "app" / "factory" / "build" / "factory_pins.lock.json"
PLATFORMS = ("manylinux_2_36_x86_64", "manylinux_2_28_x86_64", "manylinux_2_17_x86_64", "manylinux2014_x86_64")
PYTHON_VERSION = "3.12"


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def main() -> int:
    data = json.loads(LOCK.read_text(encoding="utf-8"))
    top = data["top_level_versions"]
    extras = data.get("top_level_extras", {})
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "report.json"
        cmd = [
            sys.executable, "-m", "pip", "install", "--dry-run", "--ignore-installed", "--quiet",
            "--only-binary=:all:", "--python-version", PYTHON_VERSION,
            "--target", str(Path(tmp) / "target"), "--report", str(report),
        ]
        for platform in PLATFORMS:
            cmd += ["--platform", platform]
        for dist, version in top.items():
            extra = f"[{','.join(extras[dist])}]" if dist in extras else ""
            cmd.append(f"{dist}{extra}=={version}")
        subprocess.run(cmd, check=True)
        installed = json.loads(report.read_text(encoding="utf-8"))["install"]
    found = {_norm(i["metadata"]["name"]): i["metadata"] for i in installed}
    order = [d for d in top if d in found] + sorted(d for d in found if d not in top)
    data["distributions"] = {
        d: {"version": found[d]["version"], "requires_dist": found[d].get("requires_dist", [])}
        for d in order
    }
    LOCK.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"{len(order)} distributions written to {LOCK}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
