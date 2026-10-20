"""Cross-session resume index (Item 2 PR-3).

A new chat session builds in its own workspace, so a re-run of the same brief
starts fresh even though the prior session's workspace (cloned tree, written
app/, ledger) is intact. This index maps a build's full input hash
(runner.full_input_hash = blueprint + brief + Factory sha + Store pin) to the
output_dir that built it, so a later session with the SAME inputs can point at
that workspace and let the existing reattach_point resume from the last green
stage instead of re-running COLLECTOR/CLONER/WRITER.

The index only POINTS at a candidate workspace; the resume itself is still
guarded by reattach_point (workspace intact, phases passed) and
BuildLedger.assert_resumable (the ledger's own inputs_hash must match the
re-run's), so a stale or mismatched candidate is refused and the build falls
back to fresh. A different brief/blueprint/Factory/Store is a different key, so
it never points at the wrong workspace. General: keyed only on the input hash,
never a product, brief identity or session id.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


def _index_dir() -> Path:
    from app.factory.paths import factory_outputs_root

    return factory_outputs_root() / "resume_index"


def record_resume(full_hash: str, output_dir: Path | str) -> bool:
    """Remember that ``full_hash`` was built in ``output_dir``. Best-effort:
    a write failure is swallowed (resume is an optimisation, not required)."""
    try:
        if not full_hash:
            return False
        d = _index_dir()
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / f"{full_hash}.json.tmp"
        tmp.write_text(
            json.dumps({"full_hash": full_hash, "output_dir": str(output_dir)}),
            encoding="utf-8",
        )
        tmp.replace(d / f"{full_hash}.json")
        return True
    except Exception:  # noqa: BLE001 -- indexing must never break a build
        return False


def lookup_resume(full_hash: str) -> Optional[Path]:
    """The output_dir a prior build with this exact input hash used, when it
    still exists on disk. None on any miss or read error (a miss just means a
    fresh build). The caller must still validate it is re-enterable
    (reattach_point) before resuming."""
    try:
        if not full_hash:
            return None
        path = _index_dir() / f"{full_hash}.json"
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        out = data.get("output_dir") if isinstance(data, dict) else None
        if not out:
            return None
        p = Path(out)
        return p if p.is_dir() else None
    except Exception:  # noqa: BLE001 -- a torn index entry is just a miss
        return None
