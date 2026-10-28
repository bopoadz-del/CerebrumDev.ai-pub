"""No hard-coded repo coordinates in product code (ORDERS_28OCT_0115Z #6).

The owner handle and any ``-pub`` repository name are configuration, not
source: they come from the environment via ``app.factory.repo_coords`` (no
default, fail-closed). This guard fails the build if a coordinate is baked
back into ``backend/app`` outside the test tree.
"""
from __future__ import annotations

import pathlib
import re

APP = pathlib.Path(__file__).resolve().parents[2] / "app"

# Forbidden, case-insensitive: the owner handle, and any repo name ending in
# "-pub" at a word boundary (so "-publish"/"-public" are not false positives).
FORBIDDEN_RE = re.compile(r"bopoadz|-pub\b", re.IGNORECASE)

# The Phase-2 ingestion scaffold is removed in its own PR; exclude it so this
# guard is not coupled to that PR's landing order.
EXCLUDE_DIRS = ("cerebrum_product_kernel/ingestion",)


def _scanned_files():
    for path in sorted(APP.rglob("*.py")):
        rel = path.relative_to(APP).as_posix()
        if any(rel.startswith(d) or f"/{d}" in rel or rel.startswith(d) for d in EXCLUDE_DIRS):
            continue
        if any(seg in ("tests",) for seg in path.parts):
            continue
        yield path, rel


def test_no_repo_coordinates_in_product_code():
    hits = []
    for path, rel in _scanned_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            if FORBIDDEN_RE.search(line):
                hits.append(f"{rel}:{i}: {line.strip()[:120]}")
    assert not hits, (
        "hard-coded repo coordinates found in backend/app (use app.factory.repo_coords):\n"
        + "\n".join(hits)
    )
