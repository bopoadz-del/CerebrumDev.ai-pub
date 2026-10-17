"""The writer is told where the Store gate mounts persistence.

Live 2026-10-07 (build/plt_c0b1ad1f127e43f8): the product declared
STORAGE_PATH=/data and owned it at build time, but the gate overrode the path
to /app/storage -- a directory the image never had -- so the fresh volume was
root-owned and the product died at boot. The gate now mounts at the image's
declared STORAGE_PATH (cerebrum-builds .github/store_gate/storage_mount.py);
the floor line states that contract so a writer declares the ENV the gate reads.
"""

from __future__ import annotations

import json
from pathlib import Path

FLOOR = Path(__file__).resolve().parents[2] / "app" / "factory" / "acceptance_floor.v2.json"


def _line(check_id: str) -> dict:
    data = json.loads(FLOOR.read_text(encoding="utf-8"))
    checks = data["checks"] if isinstance(data, dict) else data
    return next(c for c in checks if c.get("id") == check_id)


def test_the_floor_declares_where_the_gate_mounts_persistence():
    line = _line("single_persistence_root")
    for field in ("requirement_text", "brief_render"):
        text = line[field]
        assert "declares STORAGE_PATH as an ENV" in text
        assert "mounts its volume at exactly that declared path" in text
        assert "never overrides it" in text


def test_the_brief_line_states_the_requirement_verbatim():
    line = _line("single_persistence_root")
    assert line["brief_render"].endswith(line["requirement_text"])
