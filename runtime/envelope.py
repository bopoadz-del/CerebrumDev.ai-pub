"""The guard: the declared set is the task boundary — not a suggestion."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Set

from . import stop


class Envelope:
    def __init__(self, product_root: str):
        self.root = Path(product_root).resolve()
        self.declared: Set[str] = set()
        self.writes: List[str] = []

    def _resolve(self, rel: str) -> Path:
        p = (self.root / rel).resolve()
        if p != self.root and self.root not in p.parents:
            stop.halt("path_escape", {"path": rel}, "Paths stay inside the product workspace.")
        return p

    def guard(self, declaration: Dict[str, Any]) -> None:
        files = declaration.get("files")
        if not isinstance(files, list) or not files or not all(isinstance(f, str) and f.strip() for f in files):
            stop.halt("declaration_invalid", {"declaration": declaration}, 'The model must declare {"files": [...]} first.')
        self.declared = {self._resolve(f).relative_to(self.root).as_posix() for f in files}

    def read_path(self, rel: str) -> Path:
        return self._resolve(rel)

    def check_write(self, rel: str) -> Path:
        p = self._resolve(rel)
        if (rp := p.relative_to(self.root).as_posix()) not in self.declared:
            stop.halt("envelope_violation", {"path": rp, "declared": sorted(self.declared)}, "Escalate: the request needs files outside the declaration.")
        return p

    def record_write(self, rel: str) -> None:
        self.writes.append(self._resolve(rel).relative_to(self.root).as_posix())

    def actual_writes(self) -> List[str]:
        return sorted(set(self.writes))

    def deviations(self) -> Dict[str, List[str]]:
        return {"declared_not_written": sorted(self.declared - set(self.actual_writes())), "written": self.actual_writes()}
