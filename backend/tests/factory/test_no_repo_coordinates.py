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


def _scan(root: pathlib.Path):
    """Return ``rel:line: text`` hits for every forbidden coordinate under
    ``root`` (``*.py``), skipping any path inside a ``tests`` directory."""
    hits = []
    for path in sorted(root.rglob("*.py")):
        if any(seg == "tests" for seg in path.parts):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            if FORBIDDEN_RE.search(line):
                rel = path.relative_to(root).as_posix()
                hits.append(f"{rel}:{i}: {line.strip()[:120]}")
    return hits


def test_no_repo_coordinates_in_product_code():
    # Scans ALL of backend/app outside tests — no per-directory carve-out.
    hits = _scan(APP)
    assert not hits, (
        "hard-coded repo coordinates found in backend/app (use app.factory.repo_coords):\n"
        + "\n".join(hits)
    )


def test_the_detector_catches_a_planted_coordinate(tmp_path):
    # Known-bad sample: the detector must REPORT a planted owner handle / -pub
    # name, so a green run on the live tree is meaningful rather than vacuous.
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "leak.py").write_text(
        'REPO = "bopoadz-del/cerebrum-builds-pub"  # planted coordinate\n',
        encoding="utf-8",
    )
    planted = _scan(tmp_path)
    assert any("leak.py" in h for h in planted), (
        "detector failed to catch a planted repo coordinate"
    )
    # The live product tree stays clean.
    assert not _scan(APP)
