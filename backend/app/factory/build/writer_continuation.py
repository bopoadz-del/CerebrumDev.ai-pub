"""A writer cut at its wall while still working continues; a silent one stops.

Owner order (24 Nov 07:35Z): a writer that hits its wall WHILE MAKING
PROGRESS must not end the build as failed. The runner keeps the pass's
staging tree (the checkpoint: a pass that raises never commits and never
removes it), re-runs the WRITER phase on it, and the new pass is told what is
already done -- the steps it reported and the files on disk -- so it finishes
only what is left. A writer cut with no progress stops exactly as before.

Every input is a fact of the run itself, read from typed fields:

* the cut is the wall's own exception TYPE (``WorkerWallTimeout``), never the
  words of its message;
* progress is the coding agent's typed progress record on the ledger
  (``steps_reported`` / ``files_on_disk``, the same fields the budget inspect
  reads) inside a window that ends at the cut;
* the bounds are a maximum number of writer passes and the run's hard
  ceiling, both stated on the Floor line ("continuing, pass 2 of 3").

No product, brief or file name is read here.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

#: The run-state key holding the writer passes this run has used.
STATE_KEY = "writer_continuation"
#: The ledger payload key of the continue / stop NOTE.
NOTE_KEY = "writer_continuation"
#: The run-state key that is True only while THIS writer pass resumed a kept
#: checkout (set at every WRITER phase start; ``writer_resumed`` is sticky).
RESUMED_KEY = "writer_pass_resumed"

#: Writer passes one build may run when each cut pass was still working
#: (the first pass plus two continuations). Operator override below.
DEFAULT_MAX_PASSES = 3
#: How far back from the cut the progress record is read: "the last minutes".
DEFAULT_PROGRESS_WINDOW_S = 600.0
#: The least time a continuation pass must have under the ceiling; less than
#: this and the pass could not do anything, so the build stops as today.
DEFAULT_MIN_PASS_S = 600.0

#: How many file paths the continue section lists before it summarises.
FILES_LISTED = 200

_STEP_LINE = re.compile(r"^\s*STEP\s+(\d+)\b")


def _env_float(name: str, default: float, *, floor: float) -> float:
    try:
        value = float(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default
    return max(floor, value)


def max_writer_passes() -> int:
    """Writer passes per build (``FACTORY_WRITER_MAX_PASSES``, at least 1)."""
    return int(_env_float("FACTORY_WRITER_MAX_PASSES", DEFAULT_MAX_PASSES, floor=1))


def progress_window_s() -> float:
    """The window before the cut the progress record is read over."""
    return _env_float("FACTORY_WRITER_PROGRESS_WINDOW_S", DEFAULT_PROGRESS_WINDOW_S, floor=60.0)


def min_pass_s() -> float:
    """The least time under the ceiling a continuation pass must be given."""
    return _env_float("FACTORY_WRITER_MIN_PASS_S", DEFAULT_MIN_PASS_S, floor=60.0)


def is_wall_cut(exc: Optional[BaseException]) -> bool:
    """The writer's own wall fired: the exception, or anything it was raised
    from, is a ``WorkerWallTimeout`` by type."""
    from app.factory.build.codewhale_worker import WorkerWallTimeout

    seen = set()
    while exc is not None and id(exc) not in seen:
        if isinstance(exc, WorkerWallTimeout):
            return True
        seen.add(id(exc))
        exc = exc.__cause__ or exc.__context__
    return False


def _ts(event: Any) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(getattr(event, "ts", "") or ""))
    except ValueError:
        return None


def _int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def progress_at_cut(events: Sequence[Any], *, now: datetime, window_s: float) -> Dict[str, int]:
    """What the coding agent visibly did in the ``window_s`` before ``now``.

    ``steps``: STEP records in the window (a STEP line carries
    ``steps_reported`` and no file count; a heartbeat carries both).
    ``files_grown``: the last ``files_on_disk`` in the window minus the last
    one before it (0 when the count did not rise). Events without a readable
    timestamp are not evidence.
    """
    from app.factory.build.authorship import is_coding_agent_source
    from app.factory.build.codewhale_worker import FILES_ON_DISK, STEPS_REPORTED

    start = now - timedelta(seconds=float(window_s))
    steps = 0
    files_before: Optional[int] = None
    files_after: Optional[int] = None
    for event in events:
        payload = getattr(event, "payload", None) or {}
        if not is_coding_agent_source(payload.get("source")):
            continue
        at = _ts(event)
        if at is None:
            continue
        if at.tzinfo is None and now.tzinfo is not None:
            at = at.replace(tzinfo=now.tzinfo)
        files = _int(payload.get(FILES_ON_DISK))
        if at < start:
            if files is not None:
                files_before = files
            continue
        if at > now:
            continue
        if _int(payload.get(STEPS_REPORTED)) is not None and files is None:
            steps += 1
        if files is not None:
            files_after = files
    grown = 0
    if files_after is not None:
        grown = max(0, files_after - (files_before or 0))
    return {"steps": steps, "files_grown": grown}


@dataclass(frozen=True)
class Decision:
    """Whether the cut pass continues, and the facts the Floor line states."""

    go: bool
    reason: str
    pass_next: int
    max_passes: int
    steps: int
    files_grown: int


def decide(
    *, passes_done: int, max_passes: int, progress: Dict[str, int], staging_kept: bool
) -> Decision:
    """Continue only a pass that was working when cut, while passes remain and
    its files are still on disk. ``reason`` is a plain sentence either way."""
    steps = int(progress.get("steps") or 0)
    grown = int(progress.get("files_grown") or 0)

    def _out(go: bool, reason: str) -> Decision:
        return Decision(go, reason, passes_done + 1, max_passes, steps, grown)

    if steps <= 0 and grown <= 0:
        return _out(False, "the writer reported no step and wrote no new file in its last minutes")
    if passes_done >= max_passes:
        return _out(False, f"the writer has used all {max_passes} of its passes")
    if not staging_kept:
        return _out(False, "the writer's files from the cut pass are no longer on disk")
    return _out(True, "the writer was still working when its time ran out")


def done_steps(checkout: Path) -> Tuple[str, ...]:
    """The STEP lines of the writer's own progress log, in order, deduplicated
    by step number (a step re-reported keeps its latest line)."""
    log = Path(checkout) / "docs" / "writer_progress.log"
    try:
        text = log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ()
    by_number: Dict[int, str] = {}
    for line in text.splitlines():
        match = _STEP_LINE.match(line)
        if match:
            by_number[int(match.group(1))] = line.strip()
    return tuple(by_number[n] for n in sorted(by_number))


def done_files(checkout: Path) -> Tuple[str, ...]:
    """The files the writer has on disk, the Factory's own view of authored
    output (``agent_file_paths``), sorted."""
    from app.factory.build.codewhale_worker import agent_file_paths

    try:
        return tuple(sorted(agent_file_paths(Path(checkout))))
    except OSError:
        return ()


def render_done_section(steps: Iterable[str], files: Iterable[str], *, pass_label: str = "") -> str:
    """The ALREADY DONE block a resumed pass reads before the brief: the steps
    it reported and the files on disk. Empty when there is nothing to list."""
    steps = [s for s in steps if str(s).strip()]
    files = [f for f in files if str(f).strip()]
    if not steps and not files:
        return ""
    lines: List[str] = ["ALREADY DONE -- do not redo this work."]
    if pass_label:
        lines.append(f"This is {pass_label}.")
    lines.append(
        "Keep every file below as it is unless a test you run now fails on it. "
        "Run your existing tests first; fix only what fails, then do the steps "
        "that are not listed here."
    )
    if steps:
        lines.append(f"Steps you reported ({len(steps)}):")
        lines.extend(f"  {s}" for s in steps)
    if files:
        lines.append(f"Files on disk ({len(files)}):")
        lines.extend(f"  {f}" for f in files[:FILES_LISTED])
        if len(files) > FILES_LISTED:
            lines.append(f"  ... and {len(files) - FILES_LISTED} more")
    return "\n".join(lines) + "\n\n"


def pass_label(state: Dict[str, Any]) -> str:
    """"pass 2 of 3" while a continuation is running, else ""."""
    record = state.get(STATE_KEY) or {}
    running = _int(record.get("pass_running"))
    limit = _int(record.get("max_passes"))
    if running and limit and running > 1:
        return f"pass {running} of {limit}"
    return ""
