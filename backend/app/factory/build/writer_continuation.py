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


def _step_key(event: Any, payload: Dict[str, Any]) -> Optional[Tuple[str, Any]]:
    """Which report a STEP record is: the relayed line itself, whole (the
    same line said again is the same report, not new work); a record with
    no line falls back to its own running counter."""
    line = " ".join(str(getattr(event, "detail", "") or "").split())
    if line:
        return ("line", line)
    counter = _int(payload.get("steps_reported"))
    return None if counter is None else ("count", counter)


def _is_writer_start(event: Any) -> bool:
    kind = getattr(getattr(event, "kind", None), "value", getattr(event, "kind", None))
    role = getattr(getattr(event, "role", None), "value", getattr(event, "role", None))
    return str(kind or "") == "PHASE_STARTED" and str(role or "") == "WRITER"


def progress_at_cut(events: Sequence[Any], *, now: datetime, window_s: float) -> Dict[str, int]:
    """What the coding agent visibly did in the ``window_s`` before ``now``.

    ``steps``: steps first reported inside the window. A STEP record (it
    carries ``steps_reported`` and no file count; a heartbeat carries both)
    counts once: a line already reported earlier in the run -- said again,
    or reported by an earlier pass -- is not new work.
    ``files_grown``: the last ``files_on_disk`` in the window minus this
    pass's highest count before the window (or, with none, the window's
    first count). A count that fell and came back is not growth, and a lone
    count has nothing to compare against, so it is 0. Each WRITER phase start opens a
    new pass (its counts are relative to its own start). Events without a
    readable timestamp are not evidence.
    """
    from app.factory.build.authorship import is_coding_agent_source
    from app.factory.build.codewhale_worker import FILES_ON_DISK, STEPS_REPORTED

    start = now - timedelta(seconds=float(window_s))
    seen_steps: set = set()
    steps = 0
    peak_before: Optional[int] = None
    first_in: Optional[int] = None
    last: Optional[int] = None
    for event in events:
        if _is_writer_start(event):
            peak_before, first_in, last = None, None, None
            continue
        payload = getattr(event, "payload", None) or {}
        if not is_coding_agent_source(payload.get("source")):
            continue
        at = _ts(event)
        if at is None:
            continue
        if at.tzinfo is None and now.tzinfo is not None:
            at = at.replace(tzinfo=now.tzinfo)
        if at > now:
            continue
        files = _int(payload.get(FILES_ON_DISK))
        if _int(payload.get(STEPS_REPORTED)) is not None and files is None:
            key = _step_key(event, payload)
            if key is not None and key not in seen_steps:
                seen_steps.add(key)
                if at >= start:
                    steps += 1
        if files is None:
            continue
        if at < start:
            peak_before = files if peak_before is None else max(peak_before, files)
            continue
        if first_in is None:
            first_in = files
        last = files
    # The baseline is the highest count before the window; with none, the
    # window's own first count. A count that fell and came back is not
    # growth: only a rise above the baseline is.
    baseline = peak_before if peak_before is not None else first_in
    grown = 0
    if last is not None and baseline is not None:
        grown = max(0, last - baseline)
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
    """The lines of the writer's own progress log, in the order it wrote
    them, an exact repeat listed once. The log is the writer's own report of
    what it did, so it is passed back whole: nothing here reads its words to
    pick which lines count."""
    log = Path(checkout) / "docs" / "writer_progress.log"
    try:
        text = log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ()
    lines = (" ".join(raw.split()) for raw in text.splitlines())
    return tuple(dict.fromkeys(line for line in lines if line))


def done_files(checkout: Path) -> Tuple[str, ...]:
    """The files the writer has on disk, the Factory's own view of authored
    output (``agent_file_paths``), sorted."""
    from app.factory.build.codewhale_worker import agent_file_paths

    try:
        return tuple(sorted(agent_file_paths(Path(checkout))))
    except OSError:
        return ()


#: How many passing tests the continue section lists before it summarises.
TESTS_LISTED = 200


def done_tests(checkout: Path) -> Tuple[str, ...]:
    """The tests whose last run passed, from pytest's own record in the kept
    checkout: every node id pytest has collected (``cache/nodeids``) minus
    the ones its last run left failing (``cache/lastfailed``), keeping only
    tests whose file is still on disk. Each cache is read relative to the
    directory it sits in. Empty when pytest never ran or left no record."""
    import json

    base = Path(checkout)
    passing: List[str] = []
    for dirpath, dirnames, _files in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in {"vendor", ".git", "node_modules"}]
        if Path(dirpath).name != ".pytest_cache":
            continue
        dirnames[:] = []
        cache = Path(dirpath) / "v" / "cache"
        rootdir = Path(dirpath).parent
        try:
            nodeids = json.loads((cache / "nodeids").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        try:
            failed = json.loads((cache / "lastfailed").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            failed = {}
        if not isinstance(nodeids, list):
            continue
        failing = set(failed) if isinstance(failed, dict) else set()
        prefix = rootdir.relative_to(base).as_posix()
        for nodeid in nodeids:
            node = str(nodeid)
            if node in failing or not (rootdir / node.split("::", 1)[0]).is_file():
                continue
            passing.append(node if prefix == "." else f"{prefix}/{node}")
    return tuple(sorted(dict.fromkeys(passing)))


def render_done_section(
    steps: Iterable[str],
    files: Iterable[str],
    *,
    pass_label: str = "",
    tests: Iterable[str] = (),
) -> str:
    """The ALREADY DONE block a resumed pass reads before the brief: the steps
    it reported, the tests whose last run passed and the files on disk.
    Empty when there is nothing to list."""
    steps = [s for s in steps if str(s).strip()]
    files = [f for f in files if str(f).strip()]
    tests = [t for t in tests if str(t).strip()]
    if not steps and not files and not tests:
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
    if tests:
        lines.append(f"Tests that passed on their last run ({len(tests)}); keep them passing:")
        lines.extend(f"  {t}" for t in tests[:TESTS_LISTED])
        if len(tests) > TESTS_LISTED:
            lines.append(f"  ... and {len(tests) - TESTS_LISTED} more")
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


def floor_view(events: Sequence[Any]) -> Optional[Dict[str, Any]]:
    """The newest continue / stop decision of this run, for the build card.

    Read from the NOTE the runner wrote (its typed payload and its own
    sentence), so the card states exactly what the runner decided: the pass
    now running and its limit, the writer time used and the ceiling. None
    when this run (since its last RUN_STARTED) made no decision.
    """
    found: Optional[Any] = None
    for event in events:
        kind = getattr(getattr(event, "kind", None), "value", getattr(event, "kind", None))
        if str(kind or "") == "RUN_STARTED":
            found = None
            continue
        payload = getattr(event, "payload", None) or {}
        if isinstance(payload.get(NOTE_KEY), dict):
            found = event
    if found is None:
        return None
    record = dict((getattr(found, "payload", None) or {})[NOTE_KEY])
    ceiling = float(record.get("ceiling_s") or 0.0)
    return {
        "continuing": bool(record.get("go")),
        "pass": _int(record.get("pass_next")),
        "max_passes": _int(record.get("max_passes")),
        "writer_minutes": round(float(record.get("writer_seconds") or 0.0) / 60.0),
        "ceiling_minutes": round(ceiling / 60.0) if ceiling > 0 else None,
        "text": str(getattr(found, "detail", "") or ""),
        "ts": str(getattr(found, "ts", "") or ""),
    }
