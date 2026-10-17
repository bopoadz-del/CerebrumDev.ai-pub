"""Load CODINGPROTOCOLS.md — law is data; missing/unparsable law = halt."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from . import stop

_VERSION_RE = re.compile(r"\*\*Version\s+([0-9][^\s*]*)\*\*")


@dataclass(frozen=True)
class Law:
    version: str
    text: str
    stop_conditions: str
    anti_patterns: str


def _section(text: str, article: str) -> str:
    m = re.search(rf"## {article}[^\n]*\n(.*?)(?=\n## |\Z)", text, re.S)
    return m.group(1).strip() if m else ""


def load(path: str | None = None) -> Law:
    p = Path(path or os.getenv("PROTOCOLS_PATH", "docs/CODINGPROTOCOLS.md"))
    if not p.is_file():
        stop.halt("protocols_missing", {"path": str(p)}, "Restore CODINGPROTOCOLS.md at PROTOCOLS_PATH.")
    text = p.read_text(encoding="utf-8")
    m = _VERSION_RE.search(text)
    if not m:
        stop.halt("protocols_unparsable", {"path": str(p)}, "Stamp the law with a **Version X.Y** marker.")
    return Law(version=m.group(1), text=text, stop_conditions=_section(text, "Article 4"), anti_patterns=_section(text, "Article 5"))
