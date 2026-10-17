#!/usr/bin/env python3
"""Release gate for Role Runner Smoke Product.

Runs the platform's own *code-phase* suite (pytest -m "not pilot") and
prints a PASS/FAIL verdict. Store-backed execute-all lives on
@pytest.mark.pilot and is a later phase, not this gate.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    print("== Role Runner Smoke Product — release gate ==")
    try:
        import pytest  # noqa: F401
    except ImportError:
        print("pytest is not installed, so the suite cannot be run.")
        print("  pip install -r requirements-dev.txt")
        print("VERDICT: CANNOT RUN")
        return 2
    # Literal sys.executable. Extra braces make {sys.executable} a set,
    # which compiles then TypeError's in Popen (py_compile cannot catch it).
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "-q", "-m", "not pilot"],
        cwd=ROOT,
    )
    ok = result.returncode == 0

    lock = ROOT / "blocks.lock.json"
    if lock.is_file():
        data = json.loads(lock.read_text(encoding="utf-8"))
        blocks = data.get("blocks", {})
        print(f"vendored blocks: {len(blocks)}")
        for bid, meta in sorted(blocks.items()):
            print(f"  {bid} @ {str(meta.get('commit'))[:16]} ({meta.get('source')})")
        runtime = data.get("runtime")
        if runtime:
            print(f"store runtime slice: {len(runtime.get('files', []))} file(s) "
                  f"@ {str(runtime.get('commit'))[:16]}")
    else:
        print("blocks.lock.json: MISSING — provenance of vendored blocks is unknown")
        ok = False

    manifest = ROOT / "docs" / "build_provenance.json"
    if manifest.is_file():
        prov = json.loads(manifest.read_text(encoding="utf-8"))
        sources = prov.get("artifact_sources", {})
        agent = sorted(k for k, v in sources.items() if str(v).startswith("coder LLM"))
        print(f"artifacts: {len(sources)} total, {len(agent)} written by the coding agent")
    else:
        print("docs/build_provenance.json: MISSING")
        ok = False

    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
