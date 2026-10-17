"""Floor-monitor status fields for a build's coder session.

Surviving slice of the retired ``coder_session`` module: the runner and
the Floor monitor still stamp ``coder_control`` / ``coder_log`` /
``coder_receipt`` / ``coder_brief_present`` onto build-status, and the
owner control endpoint still writes ``docs/coder_control.json``. The
executor machinery that used to live here (FACTORY_CODE_CLI / kimi CLI /
cursor BA) is retired; the CodeWhale worker path owns execution.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BRIEF_REL = Path("docs") / "coder_brief.md"
LOG_REL = Path("docs") / "coder_session.log"
CONTROL_REL = Path("docs") / "coder_control.json"
RECEIPT_REL = Path("docs") / "coder_receipt.json"

CONTROL_RUN = "run"
CONTROL_PAUSE = "pause"
CONTROL_STOP = "stop"

FACTORY_STAGING_DIRNAME = ".factory-staging"

_VALID_ACTIONS = {CONTROL_RUN, CONTROL_PAUSE, CONTROL_STOP}


def write_control(root: Path, action: str) -> Dict[str, Any]:
    """Owner Pause / Stop / Resume. Written outside the role workspace."""
    action = str(action or "").strip().lower()
    if action == "resume":
        action = CONTROL_RUN
    if action not in _VALID_ACTIONS:
        raise ValueError(f"unknown coder control action: {action!r}")
    payload = {
        "action": action,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    path = Path(root) / CONTROL_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def read_control(root: Path) -> str:
    path = Path(root) / CONTROL_REL
    if not path.is_file():
        return CONTROL_RUN
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return CONTROL_RUN
    if not isinstance(data, dict):
        return CONTROL_RUN
    action = str(data.get("action") or "").strip().lower()
    return action if action in _VALID_ACTIONS else CONTROL_RUN


def read_receipt(root: Path) -> Dict[str, Any]:
    path = Path(root) / RECEIPT_REL
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def writer_staging_dir(output_dir: Path) -> Path:
    """RoleRunner WRITER staging sibling of ``output_dir``."""
    output_dir = Path(output_dir)
    return output_dir.parent / f".{output_dir.name}.staging-writer"


def coder_session_log_candidates(root: Path) -> List[Path]:
    """Destination log plus documented WRITER staging variants."""
    dest = Path(getattr(root, "destination", None) or getattr(root, "workspace", root))
    try:
        dest = dest.resolve()
    except OSError:
        dest = Path(dest)
    staging_attr = getattr(root, "workspace", None)
    out: List[Path] = []
    seen: set = set()

    def _add(path: Path) -> None:
        try:
            key = path.resolve()
        except OSError:
            key = path
        if key in seen:
            return
        seen.add(key)
        out.append(Path(path))

    _add(dest / LOG_REL)
    if staging_attr is not None:
        _add(Path(staging_attr) / LOG_REL)
    _add(writer_staging_dir(dest) / LOG_REL)
    parent = dest.parent
    prefix = f".{dest.name}.staging-"
    try:
        if parent.is_dir():
            for child in sorted(parent.iterdir()):
                if child.is_dir() and child.name.startswith(prefix):
                    _add(child / LOG_REL)
    except OSError:
        pass
    _add(dest / FACTORY_STAGING_DIRNAME / LOG_REL)
    return out


def resolve_coder_session_log(root: Path) -> Optional[Path]:
    """Prefer a non-empty coder log, then the newer file."""
    best_path: Optional[Path] = None
    best_key: Optional[Tuple[bool, float]] = None
    for path in coder_session_log_candidates(root):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        key = (bool(text.strip()), stat.st_mtime)
        if best_key is None or key > best_key:
            best_key = key
            best_path = path
    return best_path


def read_coder_session_log(root: Path) -> str:
    path = resolve_coder_session_log(root)
    if path is None:
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def read_log_tail(root: Path, *, max_chars: int = 8000) -> str:
    text = read_coder_session_log(root)
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def session_status(root: Path) -> Dict[str, Any]:
    """Fields stamped onto build_status for the Floor monitor."""
    control = read_control(root)
    receipt = read_receipt(root)
    brief = Path(root) / BRIEF_REL
    resolved = resolve_coder_session_log(root)
    return {
        "coder_control": control,
        "coder_log": read_log_tail(root),
        "coder_brief_present": brief.is_file(),
        "coder_log_present": bool(resolved and resolved.is_file()),
        "coder_receipt": receipt,
        "brief_dispatch": receipt.get("via") or ("compiled" if brief.is_file() else None),
    }
