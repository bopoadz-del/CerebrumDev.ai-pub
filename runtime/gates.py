"""Run the gates as subprocesses — parse exit codes and summaries, never interpret."""
from __future__ import annotations

import os
import shlex
import subprocess
from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class GateResults:
    rounds: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return bool(self.rounds) and any(g["returncode"] != 0 for g in self.rounds[-1]["gates"])

    @property
    def failed_twice(self) -> bool:
        return len(self.rounds) >= 2 and all(any(g["returncode"] != 0 for g in r["gates"]) for r in self.rounds[-2:])

    def as_list(self) -> List[Dict[str, Any]]:
        return self.rounds


def commands() -> List[str]:
    return [c.strip() for c in os.getenv("GATES_COMMANDS", "pytest").split(",") if c.strip()]


def run_all(product_root: str, results: GateResults) -> GateResults:
    gates = []
    for cmd in commands():
        try:
            argv = shlex.split(cmd)
            if not argv:
                raise ValueError("empty gate command")
            proc = subprocess.run(argv, shell=False, cwd=product_root, capture_output=True, text=True, timeout=900)
            gates.append({"command": cmd, "returncode": proc.returncode, "output_tail": ((proc.stdout or "") + (proc.stderr or "")).strip()[-2000:]})
        except Exception as exc:  # noqa: BLE001 — a gate that cannot run is a failed gate
            gates.append({"command": cmd, "returncode": -1, "output_tail": str(exc)})
    results.rounds.append({"gates": gates})
    return results
