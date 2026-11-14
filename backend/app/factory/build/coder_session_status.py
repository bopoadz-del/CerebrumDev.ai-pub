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
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

BRIEF_REL = Path("docs") / "coder_brief.md"
LOG_REL = Path("docs") / "coder_session.log"
CONTROL_REL = Path("docs") / "coder_control.json"
RECEIPT_REL = Path("docs") / "coder_receipt.json"

CONTROL_RUN = "run"
CONTROL_PAUSE = "pause"
CONTROL_STOP = "stop"

FACTORY_STAGING_DIRNAME = ".factory-staging"

_VALID_ACTIONS = {CONTROL_RUN, CONTROL_PAUSE, CONTROL_STOP}


def write_control(root: Path, action: str, *, by: Optional[str] = None) -> Dict[str, Any]:
    """Owner Pause / Stop / Resume. Written outside the role workspace.

    ``by`` is who pressed it (the authenticated principal), recorded with
    the time so a stopped build's ledger can say who stopped it and when.
    """
    action = str(action or "").strip().lower()
    if action == "resume":
        action = CONTROL_RUN
    if action not in _VALID_ACTIONS:
        raise ValueError(f"unknown coder control action: {action!r}")
    payload: Dict[str, Any] = {
        "action": action,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if by:
        payload["by"] = str(by)
    path = Path(root) / CONTROL_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def read_control_record(root: Path) -> Dict[str, Any]:
    """The whole control record (action, updated_at, by); {} when absent."""
    path = Path(root) / CONTROL_REL
    data: Any = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
    return data if isinstance(data, dict) else {}


def read_control(root: Path) -> str:
    action = str(read_control_record(root).get("action") or "").strip().lower()
    return action if action in _VALID_ACTIONS else CONTROL_RUN


# --- the owner's Stop, reachable from every stage of a running build --------
#
# The control file is written by the route; it means nothing unless the code
# doing the work reads it. A build runs on one thread (the runner's), so the
# runner installs a reader for its own control file on that thread, and every
# place that is about to spend a model call -- the writer CLI's wait loop, the
# HTTP model-call watchdog, the phase loop -- asks the same reader. One
# mechanism for every build; nothing is keyed by a build, product or stage.


class OwnerStopped(BaseException):
    """The owner pressed Stop. A BaseException on purpose: no
    ``except Exception`` between the model call and the runner may turn a
    stop into a retry, a rework or a different failure."""


_scope = threading.local()


@contextmanager
def owner_control_scope(read: Optional[Callable[[], str]]) -> Iterator[None]:
    """Install ``read`` as this thread's owner control for the block (a
    thread the build starts installs the reader it was handed)."""
    previous = getattr(_scope, "read", None)
    _scope.read = read
    try:
        yield
    finally:
        _scope.read = previous


def current_owner_control() -> Optional[Callable[[], str]]:
    """This thread's control reader (hand it to a thread you start)."""
    return getattr(_scope, "read", None)


def check_owner(read: Optional[Callable[[], str]] = None) -> None:
    """Call before a model call, and while one is in flight: raise
    ``OwnerStopped`` when the owner has pressed Stop. A thread with no
    control installed, or a reader that cannot answer, runs on (a broken
    reader never stops a build)."""
    reader = read if read is not None else current_owner_control()
    if reader is not None and _reader_action(reader) == CONTROL_STOP:
        raise OwnerStopped("owner stopped the build")


def _reader_action(reader: Callable[[], str]) -> str:
    """What the control reader answers; ``run`` when it cannot answer."""
    try:
        return str(reader() or "").strip().lower()
    except Exception:  # noqa: BLE001 -- unreadable control = run
        return CONTROL_RUN


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
