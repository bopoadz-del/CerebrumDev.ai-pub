"""Article 6 output contract — a run without a valid report is an incomplete run."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class Report:
    request_id: str
    product_id: str
    protocol_version: str
    engine_profile: str
    outcome: str = "incomplete"
    files_changed: List[str] = field(default_factory=list)
    gate_results: List[Dict[str, Any]] = field(default_factory=list)
    deviations: Dict[str, Any] = field(default_factory=dict)
    parked_items: List[Dict[str, Any]] = field(default_factory=list)
    halt: Optional[Dict[str, Any]] = None

    def as_dict(self) -> Dict[str, Any]:
        return {"request_id": self.request_id, "product_id": self.product_id, "protocol_version": self.protocol_version, "engine_profile": self.engine_profile, "outcome": self.outcome, "files_changed": self.files_changed, "gate_results": self.gate_results, "deviations": self.deviations, "parked_items": self.parked_items, "halt": self.halt, "emitted_at": datetime.now(timezone.utc).isoformat()}

    @property
    def ok(self) -> bool:
        return self.outcome == "completed" and self.halt is None


def _markdown(d: Dict[str, Any]) -> str:
    lines = [f"# Run report — {d['request_id']}", "", f"- outcome: **{d['outcome']}**", f"- product: {d['product_id']} · protocol v{d['protocol_version']} · engine: {d['engine_profile']}", "", "## FILES CHANGED"]
    lines += [f"- {f}" for f in d["files_changed"]] or ["- (none)"]
    lines += ["", "## GATE RESULTS"]
    for i, r in enumerate(d["gate_results"], 1):
        lines += [f"- round {i}: `{g['command']}` → exit {g['returncode']}" for g in r["gates"]]
    if not d["gate_results"]:
        lines.append("- (no gates ran)")
    lines += ["", "## DEVIATIONS", f"- declared not written: {d['deviations'].get('declared_not_written') or []}", "", "## PARKED ITEMS"]
    lines += [f"- {p.get('condition')}: {p.get('resume_input')}" for p in d["parked_items"]] or ["- (none)"]
    if d.get("halt"):
        lines += ["", "## HALT", f"- condition: {d['halt']['condition']}", f"- resume input: {d['halt']['resume_input']}"]
    return "\n".join(lines) + "\n"


def emit(report: Report) -> Dict[str, Any]:
    d = report.as_dict()
    out_dir = Path(os.getenv("RUNTIME_REPORT_DIR", f"runtime-runs/{report.request_id}"))
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(d, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (out_dir / "report.md").write_text(_markdown(d), encoding="utf-8")
    return d
