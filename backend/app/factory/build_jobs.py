"""Production product builds, run through the role runner.

This is the cutover seam. Until now every production door -- session
generate, the chat flow, /v1/factory/generate -- called
``ProductGenerator``, the deterministic template path: one handler template
per capability differing only in a ``BLOCK_IDS`` list, dispatching to the
operator's block store over HTTP. A downloaded product therefore could not
run without the store, and no capability logic was ever written for it.

The role runner (``app.factory.build``) is the manufacturing path: the
coding agent writes each handler, model spec and route against the block's
real contract, blocks are vendored WITH the Store runtime slice they stand
on, dispatch is in-process, and five gates judge the result. It is what
"the coding agent builds the platform" actually means.

The runner cannot run inside a request. A real build is minutes, not the
template's seconds, so it runs on a background thread and the **build
ledger is the job record** -- an append-only JSONL the runner already
fsyncs per event, which makes status a read of the artifact rather than
state held in this process. A process restart therefore loses no progress
report, and a resumed build reuses the same ledger.

Engine selection is an env switch (``FACTORY_BUILD_ENGINE``) with the
runner as the default, so a deployment can fall back to the template path
without a code change if a build regression appears in production.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger("cerebrumdev.factory.build_jobs")

BUILD_ENGINE_ENV = "FACTORY_BUILD_ENGINE"
RUNNER = "runner"
TEMPLATE = "template"

#: Wall-clock ceiling for a production Floor build. Code-only stays a
#: 20–30 min coder pass. Keyed auto-pilot starts the same ~30 min stage,
#: hard-stops, inspects, and may bump to ~45 min. An explicit leftover
#: 2h env is honoured (never slashed) but is not the default path.
BUILD_WALL_CLOCK_ENV = "FACTORY_BUILD_WALL_CLOCK_S"
BUILD_REWORK_ENV = "FACTORY_BUILD_MAX_REWORK"
BUILD_PHASE_WALL_ENV = "FACTORY_PHASE_WALL_CLOCK_S"

#: Code-only Floor run: one WRITER pass gets a full HOUR (owner ruling
#: 2026-11-21 -- 30 min was sized before the strict grader, and a producing
#: writer was being cut off mid-pass), and every granted rework round --
#: a WRITER-gate rejection or a TESTER rejection -- adds 30 min on top
#: (``budget_inspect.FIX_ALLOWANCE_S``). The phase cap must be at least the
#: first pass, or the phase box ends the pass before the plan does.
#: Auto-pilot / explicit pilot: start 30 min, inspect, optional 45 min,
#: 3 WRITER reworks. Phase cap stays 90 min so a leftover high wall is
#: not clipped by the old 25 min abort.
_DEFAULT_WALL_CLOCK_S = 3600.0
_DEFAULT_MAX_REWORK = 1
_DEFAULT_PHASE_WALL_CLOCK_S = 3600.0
_DEFAULT_PILOT_PHASE_WALL_CLOCK_S = 5400.0
#: Leftover dashboard walls in this band cannot host a Claude→DeepSeek
#: C-BRIEF session. sess_b9fbae7 died at ~47s FAILED_BUDGET_SPENT after
#: COLLECTOR's in-process OpenRouter review; remap to stage 1 when the
#: DeepSeek CLI is ready. Explicit 0 still disables. Values above this
#: band (including leftover 7200) stay honoured.
_DEEPSEEK_LEFTOVER_WALL_MAX_S = 600.0

#: A build with no ledger event for this long has no process behind it.
#: An in-flight model_call NOTE is NOT a dead process — skip this stall
#: while a live WRITER / FACTORY_CODE_CLI worker is still inside its
#: deadline (20–40 min writes are legitimate). A calling-NOTE with no
#: live worker is an orphan (deploy / restart) and must not claim
#: ``model_call_in_progress``. Overdue calls use ``_model_call_overdue``.
_STALL_AFTER_S = 1800.0

#: A build with no runner thread in this process is an orphan once the ledger
#: has been quiet this long. Threads only die with the process, so this is a
#: fact, not a guess -- unlike _STALL_AFTER_S, which has to guess.
_ORPHAN_AFTER_S = 120.0
BUILD_THREAD_ORPHANED = "BUILD_THREAD_ORPHANED"

#: How many recent activity NOTEs the Floor renders as the live log.
#: The tail, not the whole ledger: a long build writes hundreds and the
#: status payload is polled every few seconds.
_ACTIVITY_LOG_LINES = 15

#: Honesty when a model_call NOTE outlived the worker that wrote it.
#: sess_05914670d8f34533 (estate-management, tip d4a4029 / #382 deploy)
#: sat Building / model_call_in_progress for the leftover 7230s wall
#: after instance churn 7bxn4 → t5hm8 → 9lcrr killed the WRITER thread.
FACTORY_CODE_CLI_ORPHANED = "FACTORY_CODE_CLI_ORPHANED"

#: Quieter than a dead process: one coder call can sit in the model for
#: ~2–3 min with no NOTE. Past this the UI says "quiet" so a customer can
#: tell a long model call from a frozen 2/5 with no name. "may still be
#: running" is only honest while a model_call NOTE is inside its deadline.
_STALE_AFTER_S = 180.0


def build_engine() -> str:
    """Which engine production builds with. Runner unless told otherwise."""
    raw = os.getenv(BUILD_ENGINE_ENV, RUNNER).strip().lower()
    return TEMPLATE if raw in {TEMPLATE, "legacy", "generator"} else RUNNER


def _uses_pilot_budget(cycle: str = "code", auto_pilot: bool = False) -> bool:
    return auto_pilot or (cycle or "code").strip().lower() == "pilot"


def _wall_clock_s(cycle: str = "code", auto_pilot: bool = False) -> float:
    raw = os.getenv(BUILD_WALL_CLOCK_ENV)
    if raw is not None:
        try:
            value = float(raw)
        except ValueError:
            value = None
        else:
            if value == 0:
                return 0.0
            if 0 < value <= _DEEPSEEK_LEFTOVER_WALL_MAX_S:
                from app.factory.build.coder_session import deepseek_cli_ready
                from app.factory.build.roles_handlers import writer_uses_codewhale

                if deepseek_cli_ready() or writer_uses_codewhale(None):
                    return _DEFAULT_WALL_CLOCK_S
            return value
    if _uses_pilot_budget(cycle, auto_pilot):
        from app.factory.build.auto_pilot import AUTO_PILOT_WALL_CLOCK_S

        return AUTO_PILOT_WALL_CLOCK_S
    return _DEFAULT_WALL_CLOCK_S


def _max_rework(cycle: str = "code", auto_pilot: bool = False) -> int:
    raw = os.getenv(BUILD_REWORK_ENV)
    if raw is not None:
        try:
            return max(0, int(raw))
        except ValueError:
            pass
    # Owner rule: two rework rounds per build, shared by every gate, at every
    # cycle -- the runner's one rule (RoleRunner.decide) spends them.
    from app.factory.build.runner import REWORK_BUDGET

    return REWORK_BUDGET


def _phase_wall_clock_s(cycle: str = "code", auto_pilot: bool = False) -> float:
    """Per-role cap. Code-only is 60 min; Store-green uses 90 min.

    The code-only cap must be at least the writer's first pass (owner ruling
    2026-11-21: one hour, then 30 min per granted fix), or the phase box --
    not the plan wall -- is what ends the pass.

    A dashboard leftover of 1500s must not cap a keyed auto-pilot WRITER
    — that is how a 2-hour coding run died after the first handler wave.
    Explicit ``0`` still disables the bound. Values above the code-only
    default are honoured as an operator override.
    """
    raw = os.getenv(BUILD_PHASE_WALL_ENV)
    configured: Optional[float] = None
    if raw is not None:
        try:
            configured = float(raw)
        except ValueError:
            configured = None
    if _uses_pilot_budget(cycle, auto_pilot):
        if configured == 0:
            return 0.0
        if configured is None or configured <= _DEFAULT_PHASE_WALL_CLOCK_S:
            return _DEFAULT_PILOT_PHASE_WALL_CLOCK_S
        return configured
    if configured is not None:
        return configured
    return _DEFAULT_PHASE_WALL_CLOCK_S


def _phase_ref(role: Any) -> Dict[str, str]:
    """Named phase for the Floor: id plus the job title, not just 2/5."""
    from app.factory.build.authority import BuildRole, role_contract

    resolved = role if hasattr(role, "value") else BuildRole(role)
    return {"id": resolved.value, "label": role_contract(resolved).title}


def _open_model_call_note(activity_notes: Any) -> Any:
    """Latest in-flight model_call NOTE. CLI stdout must not drop the wall.

    A finished / hung-killed CLI NOTE closes the call so Floor does not
    keep 'inside watchdog' after harvest or HUNG_KILLED_BY_WALL.
    """
    from app.factory.build.model_call import closes_model_call

    for note in reversed(list(activity_notes or ())):
        payload = getattr(note, "payload", None) or {}
        if closes_model_call(payload):
            return None
        if payload.get("model_call"):
            return note
    return None


def _model_call_fields(
    last_note: Any, *, worker_live: bool = True
) -> Dict[str, Any]:
    """Surface an in-flight coder call so the Floor can bound 'quiet' copy.

    A ledger ``model_call`` NOTE is not a live worker. After a deploy the
    NOTE remains and the thread is gone — claiming in-progress then is
    the sess_05914670d8f34533 zombie (Building until the 7230s wall).
    """
    payload = (getattr(last_note, "payload", None) or {}) if last_note else {}
    if not payload.get("model_call"):
        return {}
    if not worker_live:
        return {}
    try:
        from app.factory.llm_watchdog import attempt_wall_s

        deadline = float(payload.get("deadline_s") or attempt_wall_s())
    except (TypeError, ValueError):
        from app.factory.llm_watchdog import attempt_wall_s

        deadline = attempt_wall_s()
    return {
        "model_call_in_progress": True,
        "model_call_deadline_s": round(deadline, 1),
    }


def _orphaned_inflight_model_call(
    calling_note: Any, output_dir: Path | str, *, worker_live: Optional[bool] = None
) -> bool:
    """True when a model_call NOTE has no live runner / CLI worker."""
    payload = (getattr(calling_note, "payload", None) or {}) if calling_note else {}
    if not payload.get("model_call"):
        return False
    live = _live_runner_thread(output_dir) if worker_live is None else worker_live
    return not live


def _model_call_overdue(last_note: Any, idle_s: float) -> Optional[str]:
    """Concrete timeout detail when a calling-NOTE outlived its watchdog wall."""
    fields = _model_call_fields(last_note, worker_live=True)
    if not fields:
        return None
    deadline = float(fields["model_call_deadline_s"])
    # Prefer the calling NOTE's own age. A later ledger event (or a
    # touched file mtime) must not reset the wall — that is how the
    # Floor can keep saying "may still be running" past the deadline
    # while WRITER is stuck in one model call.
    note_age = _event_age_s(getattr(last_note, "ts", "") or "", idle_s)
    age = max(float(idle_s), float(note_age))
    if age <= deadline:
        return None
    what = getattr(last_note, "detail", None) or "coder LLM call"
    return (
        f"coder LLM timed out after {int(age)}s "
        f"(deadline {int(deadline)}s) — {what}"
    )


def _event_age_s(ts: str, fallback_s: float) -> float:
    if not ts:
        return fallback_s
    try:
        from datetime import datetime, timezone

        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        import time

        return max(0.0, time.time() - parsed.timestamp())
    except ValueError:
        return fallback_s


def _event_epoch(ts: str) -> Optional[float]:
    if not ts:
        return None
    try:
        from datetime import datetime, timezone

        parsed = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def declared_deadline(events: Sequence[Any], *, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """The build's own end, from the stages it recorded.

    Every stage that opens a bounded wall is a ledger event: the run starts
    (bounded by the runner's hard ceiling -- no ramp, grant or lift passes
    it), a REWORK round or PILOT_OPENED re-opens phases under that same
    ceiling, and the N3 handoff waits on the Store gate under its own wall.
    The latest declared end is the deadline; a client polling the build reads
    ``deadline_in_s`` (server clock, so no skew) instead of guessing a number.
    Live 2026-10-08: smoke A's fixed ~58 min wait read DEAD on a healthy
    build at "2/5 writer working" on a shared one-vCPU box.
    """
    from app.factory.build.ledger import EventKind
    from app.factory.build.n3_store_gate import HANDOFF_TO_N3, n3_wall_s
    from app.factory.build.runner import BuildBudget

    ceiling = float(BuildBudget().hard_ceiling_s)
    gate_wall = float(n3_wall_s())
    end: Optional[float] = None
    anchor: Optional[str] = None
    for event in events:
        kind = getattr(event, "kind", None)
        payload = getattr(event, "payload", None) or {}
        handoff = HANDOFF_TO_N3 in (
            str(payload.get("honesty") or ""),
            str(payload.get("outcome") or ""),
        )
        if handoff:
            wall, name = gate_wall, HANDOFF_TO_N3
        elif kind in (EventKind.RUN_STARTED, EventKind.REWORK, EventKind.PILOT_OPENED):
            wall, name = ceiling, getattr(kind, "value", str(kind))
        else:
            continue
        at = _event_epoch(getattr(event, "ts", ""))
        if at is None:
            continue
        if end is None or at + wall >= end:
            end, anchor = at + wall, name
    if end is None:
        return None
    import time

    current = time.time() if now is None else float(now)
    return {
        "anchor": anchor,
        "ceiling_s": ceiling,
        "gate_wall_s": gate_wall,
        "deadline_in_s": round(end - current, 1),
    }


# -- status ---------------------------------------------------------------


def _ledger_path(output_dir: Path | str) -> Path:
    return Path(output_dir) / "build_ledger.jsonl"


CRASH_MARKER_NAME = ".build_thread_crashed"


def _crash_marker_path(output_dir: Path | str) -> Path:
    return Path(output_dir) / CRASH_MARKER_NAME


def _write_crash_marker(output_dir: Path | str, detail: str) -> None:
    try:
        _crash_marker_path(output_dir).write_text(detail.strip() + "\n", encoding="utf-8")
    except OSError:
        logger.exception("could not write crash marker at %s", output_dir)


#: The runner thread of every build this process is running, keyed by the
#: build's workspace of record. A thread NAME is not an identity: names were
#: ``build-{product_id}`` and every platform drafted without a vertical has
#: the product id "product", so one tenant's running build made every other
#: tenant's approve read "already running" and start nothing (live
#: 2026-10-06, 879ed1e1). The workspace path is unique per build.
_RUNNER_THREADS: Dict[str, threading.Thread] = {}
_RUNNER_THREADS_GUARD = threading.Lock()


def runner_key(output_dir: Path | str) -> str:
    """The identity a build's runner thread is registered under."""
    return str(Path(output_dir).resolve())


def runner_thread_name(output_dir: Path | str) -> str:
    """Log-friendly thread name derived from the same identity, never shared."""
    import hashlib

    digest = hashlib.sha256(runner_key(output_dir).encode("utf-8")).hexdigest()[:16]
    return f"build-{Path(output_dir).name}-{digest}"


def register_runner_thread(output_dir: Path | str, thread: threading.Thread) -> None:
    """Record ``thread`` as the runner of the build at ``output_dir``."""
    with _RUNNER_THREADS_GUARD:
        _RUNNER_THREADS[runner_key(output_dir)] = thread


def live_runner_thread(output_dir: Path | str) -> Optional[threading.Thread]:
    """The live runner thread of THIS build's workspace, or None."""
    with _RUNNER_THREADS_GUARD:
        thread = _RUNNER_THREADS.get(runner_key(output_dir))
        if thread is not None and not thread.is_alive():
            _RUNNER_THREADS.pop(runner_key(output_dir), None)
            thread = None
    return thread


def _live_runner_thread(output_dir: Path | str) -> bool:
    return live_runner_thread(output_dir) is not None


def _product_id_of(events: Any, output_dir: Path | str) -> str:
    from app.factory.build.ledger import EventKind

    for event in events or ():
        if event.kind is EventKind.RUN_STARTED:
            pid = str((event.payload or {}).get("product_id") or "").strip()
            if pid:
                return pid
    return Path(output_dir).name


_RUN_SUFFIX_RE = re.compile(r"__run(\d+)$")


#: What a failed run must already have finished for its workspace to be
#: re-entered rather than rebuilt.
_REATTACH_NEEDS = ("COLLECTOR", "CLONER", "WRITER")
_REATTACH_FILES = ("app", "vendor/blocks", "blocks.lock.json")


def reattach_point(output_dir: Path | str) -> tuple:
    """``(phase, "")`` when a failed run can be RE-ENTERED, else ``(None, why)``.

    G2: a resume continues, it never restarts. The old rule sent every
    RUN_FAILED to ``next_fresh_output`` -- a new ``__runN`` workspace and a full
    COLLECTOR -> CLONER -> WRITER pass -- so four resumes cost four builds. A
    failed run whose ledger shows COLLECTOR, CLONER and WRITER passed, and whose
    workspace is intact, is re-entered at its first non-passed phase instead.
    Fresh only when the workspace is missing or the ledger is unreadable.
    """
    from app.factory.build.authority import BuildRole
    from app.factory.build.ledger import BuildLedger

    out = Path(output_dir)
    try:
        ledger = BuildLedger(_ledger_path(out))
        if not ledger.exists():
            return None, "no ledger"
        done = {role.value for role in ledger.completed_roles()}
        point = ledger.resume_point()
    except Exception as exc:  # noqa: BLE001 -- a torn ledger is not a resume source
        return None, f"ledger unreadable: {type(exc).__name__}"
    missing_phases = [p for p in _REATTACH_NEEDS if p not in done]
    if missing_phases == ["WRITER"]:
        # The WRITER itself failed (a writer gate such as ui_end_to_end).
        # The old rule demanded WRITER passed because re-entry was written
        # for resuming at TESTER -- so ONE writer-gate miss sent the re-run
        # FRESH from COLLECTOR, re-collecting and re-cloning phases that had
        # already passed (live 2026-09-30: automotive-aiops). WRITER's
        # prerequisites are exactly what must be preserved: re-enter AT
        # WRITER on the cloned tree, where the writer's own progress-log
        # resume applies. Only the CLONER outputs are required -- a writer
        # that died before its first file has no app/ yet, and that is fine.
        writer_needs = [
            rel for rel in ("vendor/blocks", "blocks.lock.json")
            if not (out / rel).exists()
        ]
        if writer_needs:
            return None, "workspace incomplete: missing " + ", ".join(writer_needs)
        return "WRITER", ""
    if missing_phases:
        return None, "not passed: " + ", ".join(missing_phases)
    missing_files = [rel for rel in _REATTACH_FILES if not (out / rel).exists()]
    if missing_files:
        return None, "workspace incomplete: missing " + ", ".join(missing_files)
    if point is None:
        return None, "nothing left to run"
    return (point.value if isinstance(point, BuildRole) else str(point)), ""


def stopped_by_rule(output_dir: Path | str) -> Optional[Dict[str, Any]]:
    """The runner rule's STOP record when it ended this build, else None --
    read from the ONE terminal event (rule_decision)."""
    from app.factory.build.ledger import BuildLedger
    from app.factory.build.rule_decision import stop_record

    try:
        ledger = BuildLedger(_ledger_path(output_dir))
        terminal = ledger.terminal_event() if ledger.exists() else None
    except Exception:  # noqa: BLE001 -- a torn ledger is not a stop record
        return None
    return stop_record(terminal) if terminal is not None else None


def next_fresh_output(requested: Path | str) -> Path:
    """Sibling workspace that does not reuse a terminal-failed ledger.

    ``sessions/{id}/{product_id}`` is the first run. After RUN_FAILED the
    same path would resume a dead ledger (exhausted rework, TESTER still
    red) and the Floor would stay stopped. ``{product_id}__run2`` is a
    new auto-pilot cycle with a reset budget.
    """
    requested = Path(requested)
    parent = requested.parent
    stem = _RUN_SUFFIX_RE.sub("", requested.name)
    n = 1
    while True:
        candidate = parent / (stem if n == 1 else f"{stem}__run{n}")
        if not _ledger_path(candidate).is_file():
            return candidate
        n += 1
        if n > 10_000:
            return parent / f"{stem}__run{n}"


def _with_level_grade(status: Dict[str, Any], output_dir: Path | str) -> Dict[str, Any]:
    """Attach a fail-closed Level grade, then strip the raw failure detail from
    the Floor-facing status (J2 + J3). This is the single wire choke point for
    build_status; level grading reads only failure.phase, never the detail, so
    the strip runs after it. Import late to avoid a cycle."""
    from app.factory.build.level_grade import attach_level_grade

    graded = attach_level_grade(status, output_dir)
    stop = stop_record(graded, output_dir)
    if stop is not None:
        graded["stop"] = stop
    return _strip_floor_detail(graded)


def _event_name(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def last_failure_event(events: Sequence[Any]) -> Any:
    """The LAST failure in the ledger (a gate refusal or an aborted phase).

    A designed handoff is not a failure: a STORE refusal for a host without
    docker followed by its handoff NOTE is cleared, exactly as the phase
    trail clears it."""
    last = None
    for event in events:
        kind = _event_name(getattr(event, "kind", None))
        role = getattr(event, "role", None)
        if role is None:
            continue
        payload = getattr(event, "payload", None) or {}
        if kind in ("GATE_FAILED", "PHASE_ABORTED"):
            last = event
        elif (
            kind == "NOTE"
            and payload.get("handoff")
            and last is not None
            and getattr(last, "role", None) == role
            and (last.payload or {}).get("reason") == "docker_unavailable"
        ):
            last = None
    return last


def _elapsed_s(events: Sequence[Any]) -> Optional[int]:
    """Seconds from the first ledger event to the last."""
    stamps = [_event_epoch(getattr(e, "ts", "")) for e in events]
    stamps = [s for s in stamps if s is not None]
    if len(stamps) < 2:
        return None
    return int(max(stamps) - min(stamps))


def stop_record(status: Dict[str, Any], output_dir: Path | str) -> Optional[Dict[str, Any]]:
    """The one "ended because" record of a build that ended early, or None.

    ``kind`` is one of a closed set (failure_record.STOP_KINDS); ``step`` is
    the wire id of the step it ended in; ``steps_done``, ``fix_rounds`` and
    ``elapsed_s`` are counts. ``plain`` is one sentence with no ids in it.
    The raw message, the reason chain and the reference live under
    ``technical``. A run that wrote its terminal event is read from that
    event and the LAST failure before it; a run that never wrote one (a
    crash, a restart, a quiet build) is read from the status's own marker."""
    from app.factory.build import failure_record as fr
    from app.factory.build.authority import BUILD_PHASES
    from app.factory.build.ledger import BuildLedger
    from app.factory.build.sanitize import sanitize_for_status

    state = status.get("state")
    if state not in ("failed", "stalled"):
        return None
    try:
        ledger = BuildLedger(_ledger_path(output_dir))
        events = ledger.events()
        terminal = ledger.terminal_event()
    except Exception:  # noqa: BLE001 -- an unreadable ledger is read from the status marker
        events, terminal = [], None
    honesty = str(status.get("honesty") or "")
    last = last_failure_event(events)
    payload = (getattr(last, "payload", None) or {}) if last is not None else {}
    chain = fr.reason_chain(payload.get("reason"), getattr(last, "detail", "")) if last is not None else []
    if terminal is not None:
        tp = terminal.payload or {}
        kind, plain, code = fr.stop_kind(
            outcome=tp.get("outcome"),
            markers=fr.reason_chain(tp.get("reason"), terminal.detail),
            failure_kind=payload.get("failure_kind"),
            chain=chain,
            gate_failed=_event_name(getattr(last, "kind", None)) == "GATE_FAILED",
        )
    elif honesty in fr.STOP_ENTRIES:
        kind, plain = fr.STOP_ENTRIES[honesty]
        code = honesty
    elif state == "stalled":
        kind, plain, code = fr.STOP_NO_PROGRESS, fr.NO_PROGRESS_PLAIN, ""
    else:
        # The one failed state with no terminal event and no marker is a
        # coder call that outlived its deadline (_model_call_overdue).
        kind, plain = fr.STOP_ENTRIES["model_call_timeout"]
        code = "model_call_timeout"
    phases = [p.value for p in BUILD_PHASES]
    step = ""
    if last is not None and terminal is not None:
        step = _event_name(last.role)
    if not step:
        current = status.get("current_phase") or {}
        step = str(current.get("id") or "") if isinstance(current, dict) else ""
    if not step and terminal is not None:
        step = _event_name(getattr(terminal, "role", None))
    reference = ""
    if last is not None:
        reference = _failure_ref(
            {
                "phase": _event_name(last.role),
                "location": sanitize_for_status(payload.get("location")) or _event_name(last.role),
                "timestamp": last.ts,
                "reason": sanitize_for_status(payload.get("reason")) or "",
            }
        )
    message = getattr(last, "detail", "") if last is not None else (status.get("detail") or "")
    return {
        "kind": kind,
        "plain": plain,
        "step": step,
        "step_index": phases.index(step) + 1 if step in phases else None,
        "steps_total": len(phases),
        "steps_done": int(status.get("phases_done") or 0),
        "fix_rounds": fr.fix_rounds_used(status.get("decisions")),
        "elapsed_s": _elapsed_s(events),
        "technical": {
            "code": code,
            "reason_chain": chain,
            "message": _floor_safe_text(sanitize_for_status(message) or ""),
            "reference": reference,
        },
    }


def _cycle_fields(ledger: Any, terminal: Any) -> Dict[str, Any]:
    """Honest cycle label for the Floor: code SUCCESS is not pilot-ready."""
    payload = (getattr(terminal, "payload", None) or {}) if terminal else {}
    cycle = str(payload.get("cycle") or "").strip().lower()
    try:
        if ledger.pilot_ready():
            cycle = "pilot"
        elif ledger.pilot_cycle_open():
            cycle = "pilot"
        elif not cycle:
            cycle = "code"
    except Exception:  # noqa: BLE001
        cycle = cycle or "code"
    try:
        ready = bool(ledger.pilot_ready())
    except Exception:  # noqa: BLE001
        ready = bool(payload.get("pilot_ready"))
    # The level this run was built to, as the run recorded it (the
    # BUILD_LEVEL note at its start). It, not the deployment's environment,
    # says whether a pilot cycle follows code SUCCESS.
    try:
        from app.factory.build.build_level import ledger_bar

        level = ledger_bar(ledger.events())
    except Exception:  # noqa: BLE001
        level = None
    if level is not None:
        auto = level.get("stop_gate") not in ("", "CODE")
    else:
        try:
            from app.factory.build.auto_pilot import factory_auto_pilot_enabled

            auto = bool(factory_auto_pilot_enabled())
        except Exception:  # noqa: BLE001
            auto = False
    return {
        "cycle": cycle or "code",
        "pilot_ready": ready,
        "auto_pilot": auto,
        "build_level": level,
    }


def _handler_sources_on_disk(root: Path) -> Optional[Dict[str, str]]:
    """``{"app/actions/<id>.py": the source its AUTHORED_BY marker names}`` for
    every handler file in the tree that carries the marker, or None when none
    does (then the manifest is all there is to read)."""
    from app.factory.build.authorship import action_artifact_id, marker_source

    actions = root / "app" / "actions"
    if not actions.is_dir():
        return None
    found: Dict[str, str] = {}
    for path in sorted(actions.glob("*.py")):
        rel = f"app/actions/{path.name}"
        if action_artifact_id(rel) is None:
            continue
        try:
            source = marker_source(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        if source:
            found[rel] = source
    return found or None


def _authorship(
    output_dir: Path | str,
    *,
    blueprint: Any = None,
    plan: Any = None,
) -> Dict[str, Any]:
    """Who wrote the finished artifact, and what the agent could not write.

    The template path disclosed this in the chat message at generation time
    ("N capability(ies) shipped as honest stubs — the coder could not write
    them"). A background build cannot: nothing is stubbed yet when it
    starts. Moving the disclosure to the completion status is what keeps it
    truthful -- degraded output is acceptable, invisible degradation is not.
    """
    import json

    manifest = Path(output_dir) / "docs" / "build_provenance.json"
    if not manifest.is_file():
        return {}
    try:
        prov = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    from app.factory.build.authorship import (
        action_artifact_id,
        cli_authored_ids_from,
        coding_agent_artifact_ids,
        action_artifact_ids,
        kept_handler_ids_from,
        n_required_capabilities_from,
        writer_authorship_counts,
    )

    sources = dict(prov.get("artifact_sources") or {})
    # The handlers themselves are read off the disk when the tree carries
    # them -- the WRITER gate's rule: "counted from the workspace files the
    # writer actually produced, never from the writer's own status claim".
    # The manifest may be the writer's own (an agent-written one is left
    # alone); live cycle 9 vineyard (sess_a7c02f0cf81a4178) listed models and
    # docs but no handler, and a 22/22 Store-green build with 8 agent-stamped
    # handlers graded action_py=0. A handler file's own AUTHORED_BY marker
    # is its attribution, over whatever the manifest says: a stamped handler
    # the manifest omits counts, and one it claims whose stamp names another
    # source does not. A file with no marker keeps the manifest's word.
    on_disk = _handler_sources_on_disk(Path(output_dir))
    if on_disk:
        stamped = {action_artifact_id(rel) for rel in on_disk}
        sources = {k: v for k, v in sources.items() if action_artifact_id(k) not in stamped}
        sources.update(on_disk)
    counts = writer_authorship_counts(sources)
    agent = coding_agent_artifact_ids(sources)
    action_ids = action_artifact_ids(agent)
    # Capability work, counted apart from the factory's own plumbing. The
    # templated-majority rule compared ALL artifacts, so the substrate the
    # factory is supposed to write (alembic, authority, block_inputs, the
    # backfilled lifecycle files) counted against the agent: FinOps
    # (sess_065fc3eac75c4f62) graded "overwhelmingly templated" at 13 vs 13
    # with every one of its 8 handlers agent-written.
    all_action_ids = action_artifact_ids(sources.keys())
    dispatch = prov.get("brief_dispatch") or {}
    cli_ids = cli_authored_ids_from(dispatch)
    failures = prov.get("coder_failures") or {}
    n_required = n_required_capabilities_from(
        prov, output_dir, state=dispatch, blueprint=blueprint, plan=plan
    )
    authorship = {
        **counts,
        "agent_artifacts": agent,
        "action_py": len(action_ids),
        "action_artifacts": len(all_action_ids),
        "templated_actions": max(0, len(all_action_ids) - len(action_ids)),
        "cli_authored_ids": list(cli_ids or []),
        "kept_handler_ids": kept_handler_ids_from(dispatch),
        # Named, not counted: "3 stubs" tells the customer nothing about
        # which parts of their platform are degraded.
        "coder_failures": {k: str(v)[:300] for k, v in failures.items()},
    }
    if n_required is not None:
        authorship["n_required"] = n_required
    return {"authorship": authorship}


def _thin_authorship_detail(detail: Any) -> bool:
    """The refusal's named blocker -- the ``<BLOCKER>: <why>`` token every
    named refusal leads with -- is the thin-authorship blocker."""
    from app.factory.build.coder_session import (
        NAMED_BLOCKER_CLI_THIN_AUTHORSHIP,
        named_blocker_of,
    )

    return named_blocker_of(detail) == NAMED_BLOCKER_CLI_THIN_AUTHORSHIP


def _three_gate_success_detail(cycle: str) -> str:
    from app.factory.build.product_gate import GATE_SCOPES

    code_line = "CODE PASS — %s" % GATE_SCOPES["CODE"]
    if str(cycle or "").strip().lower() != "pilot":
        return "; ".join(
            (
                code_line,
                "PRODUCT NOT RUN — %s" % GATE_SCOPES["PRODUCT"],
                "STORE NOT RUN — %s" % GATE_SCOPES["STORE"],
            )
        )
    return "; ".join(
        (
            code_line,
            "PRODUCT PASS — %s" % GATE_SCOPES["PRODUCT"],
            "STORE PASS — %s" % GATE_SCOPES["STORE"],
        )
    )


def _reevaluate_thin_authorship_failure(
    status: Dict[str, Any],
    output_dir: Path | str,
    *,
    blueprint: Any = None,
    plan: Any = None,
) -> Dict[str, Any]:
    """Recompute the live floor on a sticky THIN_AUTHORSHIP RUN_FAILED.

    ``_finish`` replaces the three-gate SUCCESS sentence with the refuse
    string. After #391 a 4-cap golden with 4 authored ids must not stay
    locked on a baked ``need ≥5`` detail when n_required is knowable.
    """
    if not _thin_authorship_detail(status.get("detail")):
        return status
    from app.factory.build.authorship import full_pilot_authorship_from

    floor = full_pilot_authorship_from(
        status, output_dir, plan=plan, blueprint=blueprint
    )
    if floor.meets_floor:
        cycle = str(status.get("cycle") or "code").strip().lower() or "code"
        status["state"] = "succeeded"
        status["outcome"] = "SUCCESS"
        status["cycle"] = cycle
        status["pilot_ready"] = cycle == "pilot"
        status["detail"] = _three_gate_success_detail(cycle)
        status["honesty"] = "full_pilot_authorship_reevaluated"
        return status
    if floor.below_floor:
        n_req = (
            f", n_required={floor.n_required}"
            if floor.n_required is not None
            else ""
        )
        status["detail"] = (
            "FACTORY_CODE_CLI_THIN_AUTHORSHIP: authorship is below the "
            f"full-pilot floor (action_py={floor.action_py}, "
            f"cli_authored_ids={len(floor.cli_authored_ids)}, "
            f"need ≥{floor.need}{n_req}) — will not ship a "
            "Store-green / full-pilot zip"
        )
    return status


def _phase_trail(
    events: Sequence[Any],
) -> tuple[list, "dict | None"]:
    """F3: the per-phase trail plus the FIRST failure (history). What the
    Floor says stopped a build is ``_trail``'s standing failure, never this."""
    trail, history, _standing = _trail(events)
    return trail, (history[0] if history else None)


def _trail(
    events: Sequence[Any],
) -> tuple[list, list, "dict | None"]:
    """F3: per-phase trail, every failed verdict in order, and the one STANDING.

    One row per build phase in BUILD_PHASES order — never one concatenated
    string. A row carries outcome, named reason token, location, timestamp
    and detail so the Floor can render "WRITER failed — writer_no_output".

    A failed verdict stops standing when its phase passes, when a designed
    handoff replaces it, or when the run is resumed (a phase starting after a
    run verdict with no rework between them: the old stop is history). Each
    failure carries its event's typed ``failure_kind`` and the phase's run time.
    """
    from app.factory.build.authority import BUILD_PHASES
    from app.factory.build.sanitize import sanitize_for_status

    phases = [p.value for p in BUILD_PHASES]
    state: Dict[str, Dict[str, Any]] = {
        p: {
            "phase": p,
            "outcome": "not_reached",
            "reason": "",
            "location": "",
            "timestamp": None,
            "detail": "",
        }
        for p in phases
    }
    history: List[Dict[str, Any]] = []
    standing: Optional[Dict[str, Any]] = None
    started: Dict[str, Any] = {}
    ended = False
    for event in events:
        role = getattr(event, "role", None)
        kind = getattr(event, "kind", None)
        kind_name = kind.value if hasattr(kind, "value") else str(kind)
        if kind_name in ("RUN_FAILED", "RUN_SUCCEEDED"):
            ended = True
        elif kind_name == "REWORK":
            ended = False
        if role is None:
            continue
        role_name = role.value if hasattr(role, "value") else str(role)
        entry = state.get(role_name)
        if entry is None:
            continue
        if kind_name == "PHASE_STARTED":
            if ended:
                standing, ended = None, False
            started[role_name] = event.ts
            # A re-opened phase starts clean. Carrying the previous round's
            # failure text forward would render a running phase next to the
            # sentence that ended its last attempt.
            entry["outcome"] = "running"
            entry["timestamp"] = event.ts
            entry["reason"] = ""
            entry["location"] = ""
            entry["detail"] = ""
        elif kind_name == "GATE_PASSED":
            # A phase that failed, was reworked and then passed must carry
            # the PASSING verdict. Leaving the earlier round's reason and
            # detail in place made the trail read "TESTER passed" beside
            # "the round-trip check decided nothing, which is not a pass" --
            # a green phase wearing a red sentence, which is exactly the
            # shape of report this trail exists to replace.
            entry["outcome"] = "passed"
            entry["timestamp"] = event.ts
            entry["reason"] = ""
            entry["location"] = ""
            entry["detail"] = sanitize_for_status(event.detail) or ""
            if standing is not None and standing.get("phase") == role_name:
                standing = None
        elif kind_name in ("GATE_FAILED", "PHASE_ABORTED"):
            payload = event.payload or {}
            entry["outcome"] = "failed" if kind_name == "GATE_FAILED" else "aborted"
            entry["reason"] = sanitize_for_status(payload.get("reason")) or ""
            entry["location"] = sanitize_for_status(payload.get("location")) or role_name
            entry["timestamp"] = event.ts
            entry["detail"] = sanitize_for_status(event.detail) or ""
            began, stopped = _event_epoch(started.get(role_name) or ""), _event_epoch(event.ts)
            standing = {
                **entry,
                "failure_kind": str(payload.get("failure_kind") or ""),
                "ran_s": round(stopped - began) if began is not None and stopped is not None else None,
            }
            history.append(standing)
        elif kind_name == "NOTE" and (event.payload or {}).get("handoff"):
            # A designed handoff is not a failure. On a host without docker
            # the STORE gate refuses ("will not pass on a host-side skip")
            # and the runner hands the workspace to cerebrum-builds, where
            # the 13 checks run in real Docker. The ledger records that as
            # GATE_FAILED(docker_unavailable) followed by this NOTE, and the
            # trail used to stop at the first half: the Floor showed a red
            # "STORE_MANAGER failed" for the whole CI wait on a build that
            # went on to pass 13/13 -- read by the owner as a dead build.
            if entry.get("reason") == "docker_unavailable":
                # The newest failure of this phase is the verdict handed off.
                handed = next((f for f in reversed(history) if f.get("phase") == role_name), None)
                history = [f for f in history if f is not handed]
                if standing is handed:
                    standing = None
                entry["outcome"] = "handed_off"
                entry["reason"] = ""
                entry["location"] = ""
                entry["timestamp"] = event.ts
                entry["detail"] = sanitize_for_status(event.detail) or ""
    return [state[p] for p in phases], history, standing


def _crash_failure(trail: list) -> "Dict[str, Any] | None":
    """F: a crashed thread must still name the phase it died in.

    The crash handler records RUN_FAILED with no per-phase verdict, so the
    trail's last started phase reads 'running' forever. Mark it aborted
    with a named reason and return it as the failure the Floor renders.
    """
    running = next(
        (row for row in trail if row.get("outcome") == "running"), None
    )
    if running is None:
        return None
    running["outcome"] = "aborted"
    running["reason"] = "build_thread_crashed"
    running["location"] = running.get("location") or running.get("phase") or ""
    running["detail"] = "build thread crashed; see service logs"
    return {**running, "outcome": "crashed"}  # the trail row stays "aborted"


def _stop_row(terminal: Any, trail: list, standing: "Dict[str, Any] | None") -> Dict[str, Any]:
    """The row a FAILED run stopped on: the standing failure of the phase the
    terminal event names, else the terminal event itself -- a stop with no
    per-phase verdict (time budget, stage halt, checkpoint, a Stop between
    phases) is neither a crash nor nothing. Only an event with no typed
    ``outcome``, the crash handler's, is read as a crashed thread."""
    from app.factory.build.sanitize import sanitize_for_status

    payload = terminal.payload or {}
    role = terminal.role.value if getattr(terminal, "role", None) is not None else ""
    detail = sanitize_for_status(terminal.detail) or ""
    if not payload.get("outcome"):
        crashed = _crash_failure(trail)
        if crashed is not None:  # its detail is the exception the handler stamped
            return {**crashed, "detail": detail or crashed.get("detail") or ""}
        if standing is not None:
            return standing
    elif standing is not None and role in ("", standing.get("phase")):
        return standing
    # ``phase`` names a phase verdict; a stop between phases has none, only a
    # ``location`` -- so no gate is graded as failed for a step nothing judged.
    in_flight = any(row["phase"] == role and row["outcome"] == "running" for row in trail)
    return {
        "phase": role if in_flight else "",
        "outcome": "aborted",
        "reason": str(payload.get("outcome") or "").lower(),
        "location": role,
        "timestamp": terminal.ts,
        "detail": detail,
    }


# J2 (ORDERS_04NOV_2000Z): the Floor shows a plain-English SUMMARY and a REF, not
# the raw payload. The sentence comes from the stop's CLASS (failure_record: a
# closed set read from typed fields); the reason CODE is an OPEN set written
# across 20+ modules and names the outermost wrapper, not the cause. This map
# only REFINES the class sentence for a code that says more; a code without an
# entry is logged once so the map can grow from evidence. The full detail is
# filed in the ledger (the record), read back by failure_records(); the wire
# strips it (_strip_floor_detail).
_FAILURE_SUMMARIES = {
    "docker_unavailable": "The build could not reach Docker to package the product.",
    "build_thread_crashed": "The build stopped unexpectedly; the full cause is in the record.",
    "timeout": "A build step ran past its time budget before finishing.",
    "budget_skipped": "A build step was skipped because the run was out of budget.",
    "writer_behaviour_failed": "A generated capability did not accept its own request.",
    "writer_no_output": "The coder produced no output for this step.",
    "codewhale_worker_failed": "The coding agent's session ended before it finished this step.",
    "packager_failed": "The build failed while packaging the product for delivery.",
}


def _failure_code(reason: Any) -> str:
    """The stable reason token the UI branches on and the summary table is
    keyed by: the part before the first colon of the reason. Shared by the
    Floor row (_floor_failure) and the record (failure_records) so a ref maps
    to the same code in both."""
    return str(reason or "").strip().split(":", 1)[0].strip() or "build_failed"


def _failure_ref(failure: Dict[str, Any]) -> str:
    """A short, stable id the full detail is filed under (J3) and the Floor
    shows beside the summary. Derived from the row, so the same failure yields
    the same ref on every poll."""
    import hashlib

    seed = "|".join(str(failure.get(k) or "") for k in ("phase", "location", "timestamp", "reason"))
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:10]


#: Refs whose unmapped code was logged: one line per failure, not per read.
_LOGGED_FAILURE_REFS: set = set()


def _floor_failure(
    failure: "Dict[str, Any] | None",
    terminal_payload: "Dict[str, Any] | None" = None,
) -> "Dict[str, Any] | None":
    """Attach what the Floor says about a failure row (J2): ``code`` (the
    stable reason token), ``ref`` (where the full detail is filed) and the stop
    view ``kind`` / ``title`` / ``summary`` / ``next``, chosen by the stop's
    class (failure_record.stop_class), never by the code. ``detail`` is LEFT on
    the row -- failure_triple reads it before the wire boundary, where
    ``_strip_floor_detail`` removes it."""
    if not failure:
        return failure
    from app.factory.build import failure_record as fr

    code = _failure_code(failure.get("reason"))
    failure["code"] = code
    failure["ref"] = _failure_ref(failure)
    payload = terminal_payload or {}
    failure.update(
        fr.stop_view(
            fr.stop_class(payload, failure),
            ran_s=failure.get("ran_s"),
            findings=len(payload.get("findings") or ()),
        )
    )
    refined = _FAILURE_SUMMARIES.get(code)
    if refined and failure["kind"] in (fr.CHECK_REFUSED, fr.COULD_NOT_RUN, fr.UNEXPECTED):
        # Only where the failing event decided the class: a Stop or a
        # timeout is never described by an older event's code.
        failure["summary"] = refined
    elif not refined and failure["ref"] not in _LOGGED_FAILURE_REFS:
        _LOGGED_FAILURE_REFS.add(failure["ref"])
        logger.info(
            "floor failure code %s has no sentence of its own (class %s, ref %s)",
            code, failure["kind"], failure["ref"],
            extra={"code": code, "ref": failure["ref"], "phase": failure.get("phase")},
        )
    return failure


def failure_records(output_dir: Path | str) -> List[Dict[str, Any]]:
    """J3: the full per-failure detail, filed under the same ``ref`` the Floor
    shows. The Floor status carries code/summary/ref and no detail (stripped at
    the wire by ``_strip_floor_detail``); a lane turns a ref into the full
    detail by reading this off the ledger -- the job record, fsynced per event:

        from app.factory.build_jobs import failure_records
        detail = next(r["detail"] for r in failure_records(output_dir)
                      if r["ref"] == ref)

    Secrets are redacted (sanitize_for_status), nothing is truncated."""
    from app.factory.build.ledger import BuildLedger, EventKind

    ledger = BuildLedger(_ledger_path(output_dir))
    try:
        events = ledger.events()
        terminal = ledger.terminal_event()
    except Exception:  # noqa: BLE001 -- no ledger yet, or a torn one: no record, not a 500
        return []
    # One record per failure EVENT, not per phase, so a ref shown before the
    # phase failed again still resolves; plus a verdict-less stop's own row.
    trail, rows, standing = _trail(events)
    if terminal is not None and terminal.kind is EventKind.RUN_FAILED:
        stop = _stop_row(terminal, trail, standing)
        rows = rows + [stop] if all(stop is not row for row in rows) else rows
    records: List[Dict[str, Any]] = []
    for entry in rows:
        if entry.get("detail"):
            rec = dict(entry)
            rec["ref"] = _failure_ref(entry)
            rec["code"] = _failure_code(entry.get("reason"))
            records.append(rec)
    return records


def _floor_safe_text(value: Any) -> Any:
    """Cut a serialised payload out of a string the Floor shows, keeping the
    human prefix. A reason/check line can carry a JSON blob (``... HTTP 400:
    {"ok":false,...}``); the owner asked for less error detail on the UI, and
    that detail is kept in the record. The blob begins at the first ``{``; a
    newline or an overlong tail is cut too. Non-strings pass through unchanged."""
    if not isinstance(value, str) or not value:
        return value
    out = value
    brace = out.find("{")
    if brace != -1:
        out = out[:brace]
    newline = out.find("\n")
    if newline != -1:
        out = out[:newline]
    out = out.rstrip(" :-—\t").strip()
    if len(out) > 200:
        out = out[:200].rstrip(" :-—\t").strip()
    return out


def _redacted(value: Any) -> Any:
    """``value`` with keys and auth headers redacted from every string (F5)."""
    from app.factory.build.sanitize import sanitize_for_status

    if isinstance(value, str):
        return sanitize_for_status(value)
    if isinstance(value, dict):
        return {key: _redacted(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redacted(item) for item in value]
    return value


def _grader_view(events: Sequence[Any]) -> "Dict[str, Any] | None":
    """What the GRADER itself recorded in the attempt now standing, else None.

    Only an event naming the grader's gate counts: its verdict NOTE, its
    hand-back REWORK, its stop. Another gate's decision, a timeout and the
    owner's Stop carry no such name, so none can read as a grader verdict. A
    phase starting after a run verdict is a resume the grader has not judged."""
    from app.factory.build.grader import CERTIFIED, GRADER_GATE
    from app.factory.build.runner import REWORK_BUDGET

    view: Optional[Dict[str, Any]] = None
    ended = False
    for event in events:
        kind = getattr(event.kind, "value", str(event.kind))
        if kind in ("RUN_FAILED", "RUN_SUCCEEDED"):
            ended = True
            continue
        if kind == "PHASE_STARTED" and ended:
            view, ended = None, False
        payload = event.payload or {}
        if GRADER_GATE not in (payload.get("gate"), payload.get("grader_gate")):
            continue
        tier = str(payload.get("grader_tier") or (view or {}).get("tier") or "")
        state = {"REWORK": "sent_back", "GATE_FAILED": "stopped"}.get(
            kind, "certified" if tier == CERTIFIED else "refused"
        )
        view = {
            "state": state,
            "tier": tier,
            "round": int(payload.get("round") or (view or {}).get("round") or 0),
            "budget": REWORK_BUDGET,
        }
    return view


def _strip_floor_detail(status: Dict[str, Any]) -> Dict[str, Any]:
    """Remove the raw ``detail`` from everything the Floor is sent (J2 + J3).

    The owner asked for less error detail on the UI; the full detail is kept in
    the ledger and read back through ``failure_records`` under the same ref.
    This is the single wire choke point -- _with_level_grade wraps every
    build_status return -- and it runs AFTER the server-side consumers that
    need the detail (failure_triple at build_jobs.py:1264 reads failure.detail
    as its finding fallback). Stripping inside _floor_failure, as first ordered,
    would blank the FAILED finding; the wire boundary is the correct place."""
    if not isinstance(status, dict):
        return status
    stripped: List[tuple] = []  # (detail, summary) removed from the Floor rows
    for key in ("failure", "recovered_failure"):
        row = status.get(key)
        if isinstance(row, dict):
            detail = row.pop("detail", None)
            if isinstance(detail, str) and detail:
                stripped.append((detail, row.get("summary") or ""))
    # Only the FAILED/ABORTED trail entries carry the raw payload; a passed or
    # handed-off entry's detail is short and informational ("handed off",
    # "round-tripped a record") and the owner never complained of it -- keep it.
    trail = status.get("phase_trail")
    if isinstance(trail, list):
        for entry in trail:
            if isinstance(entry, dict) and entry.get("outcome") in ("failed", "aborted"):
                entry.pop("detail", None)
    # The liveness field ("Last:" on the Floor) echoes the last event's detail;
    # when that event is the failure it is the same raw payload, so show the
    # summary there instead of leaking the payload by another route.
    last_event = status.get("last_event")
    if isinstance(last_event, str) and last_event:
        for detail, summary in stripped:
            if detail in last_event:
                status["last_event"] = summary
                break
    # The rework/decisions list renders the decision's own text (d.check) and
    # the failure/trail rows can fall back to `reason`; a WRITER contract
    # refusal carries the serialised payload INSIDE those strings (the big JSON
    # dump the owner photographed under "REWORK WRITER round 1/2"). reason is
    # not a detail field, so J2/J3's detail strip never reached it -- sanitise
    # every Floor-facing text field here.
    for key in ("failure", "recovered_failure"):
        row = status.get(key)
        if isinstance(row, dict):
            for f in ("reason", "check", "finding", "location"):
                if isinstance(row.get(f), str):
                    row[f] = _floor_safe_text(row[f])
    if isinstance(trail, list):
        for entry in trail:
            if isinstance(entry, dict) and isinstance(entry.get("reason"), str):
                entry["reason"] = _floor_safe_text(entry["reason"])
    decisions = status.get("decisions")
    if isinstance(decisions, list):
        for d in decisions:
            if isinstance(d, dict):
                for f, v in list(d.items()):
                    if isinstance(v, str):
                        d[f] = _floor_safe_text(v)
    # F5 at the wire: the stop prose is redacted here, once, whichever event
    # it was copied from -- not only the trail rows someone remembered.
    for key in ("detail", "findings", "failed", "failed_label", "failure_narrative", "decisions", "stopped"):
        if key in status:
            status[key] = _redacted(status[key])
    return status


def _advisory_checks(events: Any) -> List[Dict[str, Any]]:
    from app.factory.build.brief_gates import advisory_checks

    return advisory_checks(events)


def _not_delivered(events: Any) -> List[Dict[str, Any]]:
    from app.factory.build.store_repair import not_delivered

    return not_delivered(events)


def _suggested_checks(events: Any) -> List[Dict[str, Any]]:
    from app.factory.build.foreman import suggested_checks

    return suggested_checks(events)


def queued_for_slot(notes: Sequence[Any]) -> Optional[Dict[str, Any]]:
    """``{position, ahead, since}`` while the latest slot_wait NOTE is open.

    The typed ``slot_wait`` field decides (open until the writer holds its
    slot); a later model-call NOTE also ends the wait. Never read from prose.
    """
    from app.factory.build.codewhale_worker import SLOT_WAIT_OPEN

    for note in reversed(list(notes or ())):
        payload = getattr(note, "payload", None) or {}
        if payload.get("model_call"):
            return None
        if "slot_wait" not in payload:
            continue
        if payload.get("slot_wait") != SLOT_WAIT_OPEN:
            return None
        queue = payload.get("slot_queue") or {}
        return {
            "position": queue.get("position"),
            "ahead": queue.get("ahead"),
            "since": getattr(note, "ts", None),
            "waited_s": queue.get("since_s"),
        }
    return None


def build_status(
    output_dir: Path | str,
    *,
    blueprint: Any = None,
    plan: Any = None,
) -> Dict[str, Any]:
    """Read the build's state off disk.

    Deliberately reads the LEDGER rather than any in-process registry: the
    ledger is fsynced per event by the runner, so this answer survives a
    worker restart and is the same answer the shipped artifact carries.
    """
    from app.factory.build.authority import BUILD_PHASES
    from app.factory.build.ledger import BuildLedger, EventKind
    from app.factory.build.sanitize import sanitize_for_status

    path = _ledger_path(output_dir)
    if not path.is_file():
        return {"state": "unknown", "detail": "no build ledger for this product"}

    ledger = BuildLedger(path)
    try:
        events = ledger.events()
        completed = {r.value for r in ledger.completed_roles()}
        terminal = ledger.terminal_event()
        interrupted = ledger.interrupted_role()
        resume = ledger.resume_point()
        quarantined = ledger.quarantined_notes()
    except Exception as exc:  # noqa: BLE001 -- a torn ledger must not 500
        from app.factory.build.ledger import LEDGER_UNREADABLE

        logger.warning("unreadable build ledger at %s: %s", path, exc)
        # unknown is "no ledger yet" (template / awaitBuild → succeeded).
        # An unreadable ledger is a crashed run — Floor must not keep
        # "CODING AGENT HAS TAKEN OVER" / Platforms "Building…".
        return _with_level_grade(
            {
                "state": "failed",
                "detail": f"{LEDGER_UNREADABLE}: {exc}",
                "pilot_ready": False,
                "honesty": LEDGER_UNREADABLE,
            },
            output_dir,
        )

    phases = [p.value for p in BUILD_PHASES]
    current_role = interrupted or resume
    # A crash marker is a record of a DEAD run. A live runner thread for
    # this workspace outranks it: a deploy writes the marker, the owner's
    # Continue resumes the build, and the stale marker used to make
    # build_status report "build thread crashed" for a writer that was
    # writing (live 21 Nov: a resumed build ran through COLLECTOR/CLONER
    # with nothing shown as running, while DELETE correctly refused it).
    if (
        terminal is None
        and _crash_marker_path(output_dir).is_file()
        and not _live_runner_thread(output_dir)
    ):
        trail, _history, failure = _trail(events)
        failure = _floor_failure(failure or _crash_failure(trail))
        return _with_level_grade(
            {
                "state": "failed",
                "detail": "build thread crashed; see service logs",
                "pilot_ready": False,
                "honesty": "BUILD_THREAD_CRASHED",
                # F: the Floor must still name the phase that died.
                "phase_trail": trail,
                "failure": failure,
            },
            output_dir,
        )
    if (
        terminal is None
        and interrupted is not None
        and quarantined
        and not _live_runner_thread(output_dir)
    ):
        from app.factory.build.ledger import LEDGER_EXTERNAL_NOTE_QUARANTINED

        # Live sess_69f28c0d8bc540e9: CLI scribble bricked append, the
        # crash handler could not write RUN_FAILED, the thread died, and
        # Floor kept "taken over / 1/5". After quarantine the ledger is
        # readable again — still not an active coding claim.
        return _with_level_grade(
            {
                "state": "stalled",
                "detail": (
                    f"{LEDGER_EXTERNAL_NOTE_QUARANTINED}: seq-less NOTE in "
                    "the ledger and the build thread is gone — not still coding"
                ),
                "pilot_ready": False,
                "honesty": LEDGER_EXTERNAL_NOTE_QUARANTINED,
                "ledger_quarantined_notes": quarantined,
            },
            output_dir,
        )

    if terminal is not None and terminal.kind is EventKind.RUN_SUCCEEDED:
        current_role = BUILD_PHASES[-1]
    if current_role is not None:
        phase_index = phases.index(current_role.value) + 1
        nxt = (
            BUILD_PHASES[phase_index]
            if phase_index < len(BUILD_PHASES)
            else None
        )
    else:
        phase_index = min(len(phases), sum(1 for p in phases if p in completed) + 1)
        nxt = None

    last_any = events[-1] if events else None
    notes = [e for e in events if e.kind is EventKind.NOTE]
    inspects = [
        e
        for e in notes
        if (e.payload or {}).get("budget_inspect")
        or (e.payload or {}).get("kind") == "budget_inspect"
    ]
    last_inspect = (inspects[-1].payload or {}) if inspects else None
    activity_notes = [e for e in notes if e not in inspects]
    last_note = activity_notes[-1] if activity_notes else None
    calling_note = _open_model_call_note(activity_notes)
    worker_live = _live_runner_thread(output_dir)
    try:
        import time

        file_idle_s = time.time() - path.stat().st_mtime
    except OSError:
        file_idle_s = 0.0
    # Prefer the older of ledger ts vs file mtime. Tests (and a `touch`)
    # can age one without the other; a dead process leaves both stale.
    idle_s = max(_event_age_s(last_any.ts if last_any else "", file_idle_s), file_idle_s)

    monitor: Dict[str, Any] = {
        # A writer waiting its turn for a build slot (owner, 2026-10-06: a
        # full slot queues, never fails). Read from the typed slot_wait NOTE.
        "queued": queued_for_slot(activity_notes),
        "current_phase": _phase_ref(current_role) if current_role else None,
        "phase_index": phase_index,
        "phase_total": len(phases),
        "next_phase": _phase_ref(nxt) if nxt else None,
        # F5: keys and auth headers are never rendered to the Floor.
        "last_event": sanitize_for_status(
            (last_note or last_any).detail if (last_note or last_any) else None
        ),
        "last_event_at": last_any.ts if last_any else None,
        "last_event_age_s": round(idle_s, 1),
        "stale": idle_s > _STALE_AFTER_S,
        # The writer narrates its pass (STEP lines relayed from
        # docs/writer_progress.log) and every line already lands here as a
        # NOTE -- but only the newest one used to reach the Floor, so the
        # operator saw a single sentence replaced every few minutes and
        # could not tell a working agent from a wedged one. Ship the tail
        # so the pass reads as a live log. Sanitized like last_event: keys
        # and auth headers are never rendered (F5).
        "activity_log": [
            {
                "ts": note.ts,
                "role": (
                    note.role.value
                    if getattr(note.role, "value", None)
                    else str(note.role or "")
                ),
                "text": sanitize_for_status(note.detail) or "",
            }
            for note in activity_notes[-_ACTIVITY_LOG_LINES:]
            if sanitize_for_status(note.detail)
        ],
        **_model_call_fields(calling_note or last_note, worker_live=worker_live),
    }
    if last_note is not None:
        payload = last_note.payload or {}
        done, total = payload.get("done"), payload.get("total")
        if isinstance(done, int) and isinstance(total, int) and total > 0:
            monitor["phase_progress"] = {
                "done": done,
                "total": total,
                "fraction": round(done / total, 3),
                "stage": payload.get("stage"),
            }

    from app.factory.build.coder_session_status import session_status

    # The failure shown is the verdict STANDING now, never the first one the
    # ledger saw; a stopped run reads the row its NEWEST verdict stopped on,
    # so the label, the red chip, the Continue line and the ref agree.
    phase_trail, history, failure = _trail(events)
    stop_payload: Dict[str, Any] = {}
    from app.factory.build.n3_store_gate import handoff_awaiting_n3

    # A designed handoff ends its cycle with a terminal event too; it is a
    # wait for the Store gate, not a stop, and gets no stop row.
    handing_off = (
        terminal is not None
        and terminal.kind is EventKind.RUN_FAILED
        and handoff_awaiting_n3(output_dir)
    )
    if terminal is not None and terminal.kind is EventKind.RUN_FAILED and not handing_off:
        stop_payload = dict(terminal.payload or {})
        failure = _stop_row(terminal, phase_trail, failure)
    # J2: the Floor failure row carries code / summary / ref; the raw detail
    # stays on the row for the build record (J3), never in the Floor string.
    failure = _floor_failure(failure, stop_payload)
    # A run whose verdict is SUCCESS has no current failure; the first one it
    # recovered from is history, named for what it is (sess_617f60024df24a4e
    # showed a red "TESTER failed -- KeyError" under a 13/13 PASS header).
    recovered_failure = None
    if terminal is not None and terminal.kind is EventKind.RUN_SUCCEEDED:
        recovered_failure = _floor_failure(dict(history[0])) if history else None
        failure = None
    progress = {
        "phases": phases,
        "completed": [p for p in phases if p in completed],
        "phases_total": len(phases),
        "phases_done": sum(1 for p in phases if p in completed),
        "ledger_quarantined_notes": quarantined,
        # F3: per-phase outcome trail + the failure's exact location, named.
        "phase_trail": phase_trail,
        "failure": failure,
        "recovered_failure": recovered_failure,
        # Every check this build moved to advisory -- a gate its brief never
        # defined -- with the reason, so none is silenced out of sight.
        "advisory_checks": _advisory_checks(events),
        # F4/F2: checks the foreman PROPOSED for a human to accept into the
        # floor -- read-only suggestions, never a verdict.
        "suggested_checks": _suggested_checks(events),
        # F-3c (a): capabilities the ledger marks not delivered (a Store
        # block whose repair failed), block and reason -- read-only.
        "not_delivered": _not_delivered(events),
        # The runner rule's every decision (gate, class, round n/2 and build
        # n/6, check, finding) and, when it stopped the build, the stop.
        # Only records carrying the rule's CLASS: the owner-stop and
        # time-budget records share the key and rendered as a blank row.
        "decisions": [
            dict((e.payload or {})["decision"])
            for e in events
            if isinstance((e.payload or {}).get("decision"), dict)
            and (e.payload or {})["decision"].get("class")
        ],
        # The grader's state, from the events the grader itself wrote.
        "grader": _grader_view(events),
        "stopped": stopped_by_rule(output_dir),
        # The build's own declared end (server clock); a poller waits on
        # this, never on a number of its own.
        "deadline": declared_deadline(events),
        **monitor,
        **_cycle_fields(ledger, terminal),
        **session_status(Path(output_dir)),
    }
    if last_inspect:
        from app.factory.build.budget_inspect import (
            reconcile_budget_inspect_after_success,
        )

        ready_now = False
        if terminal is not None and terminal.kind is EventKind.RUN_SUCCEEDED:
            try:
                ready_now = bool(ledger.pilot_ready())
            except Exception:  # noqa: BLE001
                ready_now = False
        progress["budget_inspect"] = (
            reconcile_budget_inspect_after_success(
                last_inspect, pilot_ready=True
            )
            if ready_now
            else last_inspect
        )

    if terminal is not None and terminal.kind is EventKind.RUN_SUCCEEDED:
        payload = terminal.payload or {}
        succeeded = {
            "state": "succeeded",
            "detail": terminal.detail,
            "cycle": payload.get("cycle") or "code",
            "outcome": payload.get("outcome"),
            # Only a SUCCESS that closed a pilot cycle is Store-green / pilot-ready.
            # Code-phase success must not be presented as a finished pilot.
            "pilot_ready": ledger.pilot_ready(),
            **progress,
            **_authorship(output_dir, blueprint=blueprint, plan=plan),
            "stale": False,
        }
        if payload.get("honesty"):
            succeeded["honesty"] = payload.get("honesty")
        if payload.get("score"):
            succeeded["n3_score"] = payload.get("score")
        if payload.get("builds_sha"):
            succeeded["builds_sha"] = payload.get("builds_sha")
        succeeded["delivery_format"] = payload.get("delivery_format") or "zip"
        if payload.get("repo_url"):
            succeeded["repo_url"] = payload.get("repo_url")
        return _with_level_grade(succeeded, output_dir)
    if terminal is not None and terminal.kind is EventKind.RUN_FAILED:
        payload = terminal.payload or {}
        if handing_off:
            # Receipt accepted; N3 store-gate is the next green. Do not paint
            # this as a terminal coding failure or unlock package.
            waiting = {
                "state": "building",
                "detail": (
                    "Handed off to the Store gate: this host has no Docker, so "
                    "the acceptance checks are running in Docker on "
                    "cerebrum-builds. Nothing has failed; this takes a few "
                    "minutes."
                ),
                "cycle": payload.get("cycle") or "code",
                "outcome": "HANDOFF_TO_N3",
                "honesty": "HANDOFF_TO_N3",
                "next": "n3_gate",
                "green": False,
                "n3_waiting": True,
                "pilot_ready": False,
                "findings": list(payload.get("findings") or [])[:10],
                **progress,
                **_authorship(output_dir, blueprint=blueprint, plan=plan),
                "stale": False,
            }
            for key in (
                "builds_sha",
                "builds_branch",
                "builds_owner",
                "builds_repo",
            ):
                if payload.get(key):
                    waiting[key] = payload[key]
            return _with_level_grade(waiting, output_dir)
        from app.factory.build.failure_record import failed_label, failure_triple
        from app.factory.build.failure_narrative import read_narrative

        triple = failure_triple(payload, progress.get("failure"))
        failed = {
            "state": "failed",
            "detail": terminal.detail,
            "cycle": payload.get("cycle") or "code",
            "outcome": payload.get("outcome"),
            "pilot_ready": False,
            "findings": list(payload.get("findings") or [])[:10],
            "delivery_format": payload.get("delivery_format") or "zip",
            **progress,
            **_authorship(output_dir, blueprint=blueprint, plan=plan),
            "stale": False,
            # A failed build is still a product: it exports as-is, and every
            # surface names the same FAILED(gate, check, finding). Never green.
            "certified": False,
            "failed": triple,
            "failed_label": failed_label(triple) if triple else None,
            # What continuing does for THIS kind of stop -- never a check id.
            "next_continue": (progress.get("failure") or {}).get("next") or "",
            # F5: what was tried, where it stopped, why -- from the ledger.
            "failure_narrative": read_narrative(output_dir),
        }
        if payload.get("repo_url"):
            failed["repo_url"] = payload.get("repo_url")
        if payload.get("honesty"):
            failed["honesty"] = payload.get("honesty")
        if _thin_authorship_detail(terminal.detail):
            failed = _reevaluate_thin_authorship_failure(
                failed, output_dir, blueprint=blueprint, plan=plan
            )
        return _with_level_grade(failed, output_dir)
    # Intra-phase activity. Without this a WRITER pass of ~16 agent calls
    # reports a frozen "2/5" for twenty minutes and a customer cannot tell
    # work from a hang.
    activity: Dict[str, Any] = {}
    if last_note is not None:
        activity = {
            "activity": sanitize_for_status(last_note.detail),
            "activity_stage": (last_note.payload or {}).get("stage"),
            "activity_done": (last_note.payload or {}).get("done"),
            "activity_total": (last_note.payload or {}).get("total"),
        }

    # A calling-NOTE that outlived the watchdog wall means the WRITER thread
    # is stuck in a model call that will never complete. Fail now so the
    # Floor shows CODING AGENT STOPPED instead of "quiet for N min".
    overdue = _model_call_overdue(calling_note or last_note, idle_s)
    if overdue:
        return _with_level_grade(
            {
                "state": "failed",
                "detail": overdue,
                "pilot_ready": False,
                **progress,
                **activity,
                "stale": False,
                "model_call_in_progress": False,
            },
            output_dir,
        )

    # Deploy / worker restart mid-WRITER leaves the model_call NOTE and
    # the 7230s C-BRIEF wall, but no thread and no FACTORY_CODE_CLI.
    # Do not wait for the deadline — that is the estate-management zombie.
    if _orphaned_inflight_model_call(
        calling_note or last_note, output_dir, worker_live=worker_live
    ):
        return _with_level_grade(
            {
                "state": "stalled",
                "detail": (
                    f"{FACTORY_CODE_CLI_ORPHANED}: no live WRITER / "
                    "FACTORY_CODE_CLI worker after process restart — "
                    "model call is not in progress"
                ),
                "pilot_ready": False,
                "honesty": FACTORY_CODE_CLI_ORPHANED,
                **progress,
                **activity,
                "model_call_in_progress": False,
            },
            output_dir,
        )

    # A build whose thread died (worker restart, OOM, redeploy) leaves the
    # ledger's last event as PHASE_STARTED forever, which read as "building"
    # for eternity. Age the file: no event for this long means nothing is
    # working on it, and saying so is the honest answer.
    # An in-flight coder call inside its deadline is work, not a dead
    # process — stalling at 30 min would abort a legitimate 40 min write.
    in_flight_call = bool(
        _model_call_fields(calling_note or last_note, worker_live=worker_live)
    )
    # No build thread in this process and the ledger has gone quiet: the
    # server restarted under the build. Say so now. Waiting out the 30 min
    # below left the owner locked out of a dead build -- the Floor disables
    # its message box while a build reads "building", so "continue" could not
    # even be typed (live: FleetOps, server exited 09:54, still "building"
    # 20 minutes later). The grace period covers the gap between the code
    # cycle's thread ending and the pilot cycle's thread starting.
    if not worker_live and idle_s > _ORPHAN_AFTER_S:
        return _with_level_grade(
            {
                "state": "stalled",
                "detail": (
                    "the factory server restarted while this build was running, "
                    "so nothing is working on it. Your progress up to the last "
                    "finished phase is saved -- reply 'continue' to resume"
                ),
                "pilot_ready": False,
                "honesty": BUILD_THREAD_ORPHANED,
                **progress,
                **activity,
            },
            output_dir,
        )
    if idle_s > _STALL_AFTER_S and not in_flight_call:
        return _with_level_grade(
            {
                "state": "stalled",
                "detail": (
                    f"no build activity for {int(idle_s // 60)} min — the build "
                    "process is gone (restart or redeploy); generate again"
                ),
                "pilot_ready": False,
                **progress,
                **activity,
            },
            output_dir,
        )

    return _with_level_grade(
        {"state": "building", "detail": "build in progress", **progress, **activity},
        output_dir,
    )


#: What the session list says of a session's build, one word each.
OUTCOME_BUILDING = "building"
OUTCOME_STOPPED = "stopped"
OUTCOME_READY = "ready"
OUTCOME_NOT_FOUND = "not_found"


def build_outcome(output_dir: Path | str) -> Optional[str]:
    """Building, stopped, ready or not found, for the session list.

    A light read of the ledger's terminal event and its age -- no grading,
    no authorship scan -- because it runs once per listed session. None when
    the workspace has no runner ledger (a template build)."""
    import time

    from app.factory.build.ledger import BuildLedger, EventKind

    out = Path(output_dir)
    if not out.is_dir():
        return OUTCOME_NOT_FOUND
    path = _ledger_path(out)
    if not path.is_file():
        return None
    try:
        terminal = BuildLedger(path).terminal_event()
        idle_s = time.time() - path.stat().st_mtime
    except Exception:  # noqa: BLE001 -- a torn ledger is a stopped build
        return OUTCOME_STOPPED
    if terminal is not None and terminal.kind is EventKind.RUN_SUCCEEDED:
        return OUTCOME_READY
    if terminal is not None:
        from app.factory.build.n3_store_gate import handoff_awaiting_n3

        return OUTCOME_BUILDING if handoff_awaiting_n3(out) else OUTCOME_STOPPED
    if _live_runner_thread(out):
        return OUTCOME_BUILDING
    if _crash_marker_path(out).is_file() or idle_s > _ORPHAN_AFTER_S:
        return OUTCOME_STOPPED
    return OUTCOME_BUILDING


def is_build_complete(output_dir: Path | str) -> bool:
    """True only for a finished, successful build.

    The download path gates on this. Zipping a workspace mid-build would
    hand the customer a splice of two writer passes -- the exact torn
    artifact the runner's staging protects against internally.
    """
    return build_status(output_dir).get("state") == "succeeded"


# -- starting a build -----------------------------------------------------


def _quota_marker(output_dir: Path) -> Path:
    return Path(output_dir) / ".generation_quota_account"


def _write_quota_marker(output_dir: Path, account_id: Optional[str]) -> None:
    if not account_id:
        return
    _quota_marker(output_dir).write_text(account_id, encoding="utf-8")


def _clear_quota_marker(output_dir: Path) -> None:
    try:
        _quota_marker(output_dir).unlink()
    except FileNotFoundError:
        return


def _refund_generation_quota(output_dir: Path) -> None:
    path = _quota_marker(output_dir)
    if not path.is_file():
        return
    try:
        account_id = path.read_text(encoding="utf-8").strip()
    except OSError:
        return
    if account_id:
        from app.core.trial_limits import refund

        refund(account_id, "generation")
    _clear_quota_marker(output_dir)


class CanonicalRefused(RuntimeError):
    """A canonical copy was not written. ``reason`` is the typed token."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason


#: Why a canonical copy is refused. One token per rule in ``clone_canonical``.
CANONICAL_OPERATOR_ONLY = "canonical_operator_only"
CANONICAL_UNSAFE_NAME = "canonical_unsafe_name"
CANONICAL_OVERLAPS_SOURCE = "canonical_overlaps_source"
CANONICAL_NOT_CREATED_HERE = "canonical_not_created_here"
CANONICAL_OTHER_OWNER = "canonical_other_owner"

#: Written into every canonical copy ``clone_canonical`` creates. It is the
#: only proof a directory IS a canonical copy, and it names the product the
#: copy belongs to. A directory without it was made by something else.
CANONICAL_MARKER = ".canonical_copy.json"


def _canonical_owner(dest: Path) -> Optional[str]:
    """The product the canonical copy at ``dest`` belongs to.

    None when ``dest`` is not a copy ``clone_canonical`` wrote: no marker, or
    one that does not read as its own.
    """
    try:
        data = json.loads((dest / CANONICAL_MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    owner = data.get("product_id") if isinstance(data, dict) else None
    return owner if isinstance(owner, str) and owner else None


def is_operator_run(output_dir: Path | str, *, tenant_bound: bool) -> bool:
    """Whether a build is an operator's own run and not a customer's.

    A customer's build is bound to a tenant, or its workspace is a session's,
    or both. Only a run that is neither may honour an operator-only blueprint
    field (``clone_canonical``).
    """
    from app.factory.build.orphan_recovery import session_id_from_output

    return not tenant_bound and session_id_from_output(output_dir) is None


def clone_canonical(
    blueprint: Any, source_dir: Path | str, *, operator_run: bool
) -> Optional[Path]:
    """Copy a finished tree to factory_outputs/<canonical_output>.

    A blueprint that wants a stable canonical copy says so itself
    (``canonical_output``); no product is named here. A second runner used to
    start here and double LLM spend. Clone only.

    ``canonical_output`` is an operator-only field (``blueprint.SetBy``), and
    this is the one place that deletes and replaces a directory because of
    it. So it writes only what is provably its own, and refuses the rest with
    a typed reason (``CanonicalRefused``):

    * a run that belongs to a customer (``operator_run`` is False, see
      ``is_operator_run``) -- the field is not theirs to set, whatever
      blueprint reached the build;
    * a name that is not one plain directory directly under the outputs root
      once resolved (``paths.canonical_copy_dir``);
    * a target that is, holds, or sits inside the tree being copied;
    * an existing directory this function did not create (no marker), or one
      it created for another product.

    ``operator_run`` has no default: every caller states whose run it is.
    """
    from app.factory.paths import UnsafeOutputDir, canonical_copy_dir

    name = str(getattr(blueprint, "canonical_output", None) or "").strip()
    if not name:
        return None
    if not operator_run:
        raise CanonicalRefused(
            CANONICAL_OPERATOR_ONLY,
            "canonical_output is an operator-only field; this run belongs to a customer",
        )
    try:
        dest = canonical_copy_dir(name)
    except UnsafeOutputDir as exc:
        raise CanonicalRefused(CANONICAL_UNSAFE_NAME, str(exc)) from exc
    src = Path(source_dir).resolve()
    if dest == src or dest in src.parents or src in dest.parents:
        raise CanonicalRefused(
            CANONICAL_OVERLAPS_SOURCE,
            f"{dest} is, holds, or sits inside the tree being copied ({src})",
        )
    owner = str(getattr(blueprint, "product_id", "") or "")
    if dest.exists():
        held_by = _canonical_owner(dest)
        if held_by is None:
            raise CanonicalRefused(
                CANONICAL_NOT_CREATED_HERE,
                f"{dest} exists and is not a canonical copy this writer created; "
                "it is left untouched",
            )
        if held_by != owner:
            raise CanonicalRefused(
                CANONICAL_OTHER_OWNER,
                f"{dest} is the canonical copy of another product; it is left untouched",
            )
        shutil.rmtree(dest)
    # The marker lands before the tree, so a copy interrupted half-way is
    # still recognisably this writer's and the next run can replace it. A
    # marker inside the source is never carried over.
    dest.mkdir(parents=True)
    (dest / CANONICAL_MARKER).write_text(
        json.dumps({"product_id": owner}), encoding="utf-8"
    )
    shutil.copytree(
        src, dest, dirs_exist_ok=True, ignore=shutil.ignore_patterns(CANONICAL_MARKER)
    )
    return dest


def _maybe_clone_canonical(
    blueprint: Any, output_dir: Path, *, operator_run: bool
) -> None:
    try:
        dest = clone_canonical(blueprint, output_dir, operator_run=operator_run)
        if dest is not None:
            logger.info("cloned canonical copy to %s", dest)
    except CanonicalRefused as exc:
        # A refusal is a decision, not a crash: one line, no traceback.
        logger.warning("canonical copy refused from %s: %s", output_dir, exc)
    except Exception:  # noqa: BLE001 — clone must not fail the build record
        logger.exception("canonical clone failed from %s", output_dir)


def _run(
    blueprint: Any,
    output_dir: Path,
    blocks_root: Optional[Path],
    cycle: str = "code",
    tenant_store: Any = None,
    brief: str = "",
    inputs_hash: str = "",
) -> None:
    from app.factory.build.auto_pilot import factory_auto_pilot_enabled
    from app.factory.build.build_level import bar_for
    from app.factory.build.runner import BuildBudget, RoleRunner

    # The user's build level is the single input for whether a pilot cycle
    # follows code SUCCESS. Only a blueprint that declares no level (a direct,
    # non-Floor caller) falls back to the operator's FACTORY_AUTO_PILOT.
    bar = bar_for(blueprint)
    climbs = bar.reaches_pilot if bar is not None else factory_auto_pilot_enabled()
    auto = cycle == "code" and climbs
    # THIS BUILD's session id, resolved into a LOCAL and threaded through the
    # runner — never written back into os.environ.
    #
    # This used to do `os.environ["FACTORY_SESSION_ID"] = sid` from inside the
    # per-build THREAD, and nothing ever cleared it. Two ways that is wrong,
    # and only one of them needs concurrency:
    #   LOST WRITE (already live at a cap of 1): build A sets the global and
    #     never unsets it, so build B finds it non-empty, takes the `if not`
    #     branch as false, and runs its whole lifetime under A's session id.
    #     Every build after the first in a process inherited the first one's.
    #   CLOBBER (needs concurrency): two builds both find it empty, both
    #     write, last writer wins for both.
    # The session id is per-build state and the process environment is not a
    # place to keep per-build state in a multi-tenant process. An externally
    # set FACTORY_SESSION_ID is still honoured — it is a deliberate operator
    # override — it is just no longer written by us.
    from app.factory.build.orphan_recovery import session_id_from_output

    session_id = (
        str(os.getenv("FACTORY_SESSION_ID") or "").strip()
        or session_id_from_output(output_dir)
        or ""
    )
    max_rework = _max_rework(cycle, auto_pilot=auto)
    try:
        # The plan is computed ONCE here and handed in: the plan-time budget
        # class (0605Z owner ruling) comes from the planner's no-Store-block
        # share, and the runner must build against the SAME plan whose class
        # chose the wall.
        from app.factory.planner import CapabilityPlanner, assert_generatable

        # Same resolution the runner does for a falsy root, BEFORE planning:
        # the planner's class computation must see the same Store the build
        # will clone from.
        if not blocks_root:
            from app.factory.blocks_source import resolve_blocks_root

            blocks_root = resolve_blocks_root()
        plan = assert_generatable(CapabilityPlanner(blocks_root).plan(blueprint))
        wall = _wall_clock_s(cycle, auto_pilot=auto)
        if not os.getenv(BUILD_WALL_CLOCK_ENV):
            # No operator-pinned wall: the plan class picks it (30 min small,
            # 40 min new-platform). One config, no literals here.
            from app.factory.build.budget_inspect import NEW_PLATFORM_WALL_S

            if str(getattr(plan, "budget_class", "small") or "small") == "new_platform":
                wall = NEW_PLATFORM_WALL_S
        runner = RoleRunner(
            blueprint,
            output_dir,
            blocks_root=blocks_root,
            plan=plan,
            budget=BuildBudget(
                max_rework=max_rework,
                wall_clock_s=wall,
                phase_wall_clock_s=_phase_wall_clock_s(
                    cycle, auto_pilot=auto
                ),
            ),
            cycle=cycle,
            auto_pilot=auto if cycle == "code" else False,
            tenant_store=tenant_store,
            brief=brief,
            session_id=session_id,
            inputs_hash=inputs_hash or None,
        )
        # F-3c (b): the Store repairer and installer, only when the one
        # setting (FACTORY_STORE_REPAIR) is on; off leaves the runner as is.
        from app.factory.build.store_repair_wiring import arm as arm_store_repair

        arm_store_repair(runner)
        outcome = runner.run()
        logger.info(
            "runner build finished: product=%s outcome=%s rework=%s",
            getattr(blueprint, "product_id", "unknown"),
            outcome.outcome.value if hasattr(outcome.outcome, "value") else outcome.outcome,
            outcome.rework_used,
        )
        from app.factory.build.runner import Outcome as RunnerOutcome

        if outcome.outcome is RunnerOutcome.HANDOFF_TO_N3:
            # Receipt accepted after TESTER + STORE_MANAGER. N3 store-gate
            # is next. Keep the generation charge (not a fail) and do not
            # clone a non-green Steward tree. Poll cerebrum-builds commit
            # status in this same thread — never re-enter WRITER or launch
            # another Background Agent.
            _clear_quota_marker(output_dir)
            try:
                from app.factory.build.n3_store_gate import wait_and_ingest_n3

                wait_and_ingest_n3(output_dir)
            except Exception:  # noqa: BLE001 — waiter crash must not kill the thread
                logger.exception("n3 store-gate ingest failed for %s", output_dir)
                return
            # The N3 Store gate answered after the runner returned. Its
            # product-owned verdict goes through the runner's ONE rule
            # (RoleRunner.decide): advisory / rework / stop, per-gate budget,
            # global same-failure-twice. A REWORK re-opens WRITER, TESTER and
            # STORE_MANAGER on this workspace and the runner runs again.
            from app.factory.build.ledger import BuildLedger, EventKind
            from app.factory.build.n3_store_gate import store_gate_verdict
            from app.factory.build.runner import DECISION_REWORK

            terminal = BuildLedger(_ledger_path(output_dir)).terminal_event()
            verdict = (
                store_gate_verdict(terminal.payload or {})
                if terminal is not None and terminal.kind is EventKind.RUN_FAILED
                else None
            )
            if (
                verdict is not None
                and runner.reopen_after_store_gate(verdict).kind == DECISION_REWORK
            ):
                logger.info("store-gate rework: WRITER re-opened at %s", output_dir)
                _run(
                    blueprint,
                    output_dir,
                    blocks_root,
                    cycle,
                    tenant_store,
                    brief,
                    inputs_hash,
                )
            return
    except Exception as exc:  # noqa: BLE001
        # The thread must never die silently: without this the ledger's last
        # event stays PHASE_STARTED and status reads "building" forever.
        logger.exception("runner build crashed for %s", output_dir)
        from app.factory.build.sanitize import sanitize_for_status

        crash_detail = "build thread crashed: " + sanitize_for_status(
            str(exc) or type(exc).__name__
        )
        crash_detail = crash_detail[:400]
        try:
            from app.factory.build.ledger import BuildLedger, EventKind

            ledger = BuildLedger(_ledger_path(output_dir))
            crashed_role = ledger.interrupted_role()
            ledger.append(
                EventKind.RUN_FAILED,
                detail=crash_detail,
                payload={
                    "reason": "build_thread_crashed",
                    "location": crashed_role.value if crashed_role else "",
                    "exception": sanitize_for_status(type(exc).__name__),
                },
            )
        except Exception:  # noqa: BLE001
            logger.exception("could not record the crash in the ledger")
            _write_crash_marker(output_dir, crash_detail)
        _refund_generation_quota(output_dir)
        return
    from app.factory.build.ledger import BuildLedger, EventKind

    terminal = BuildLedger(_ledger_path(output_dir)).terminal_event()
    failed = terminal is not None and terminal.kind is EventKind.RUN_FAILED
    if failed:
        _refund_generation_quota(output_dir)
        return
    # A build bound to a tenant store, or built in a session's workspace, is
    # a customer's. Only an operator's own run may honour an operator-only
    # blueprint field.
    _maybe_clone_canonical(
        blueprint,
        output_dir,
        operator_run=is_operator_run(output_dir, tenant_bound=tenant_store is not None),
    )
    _clear_quota_marker(output_dir)


def _record_platform(ledger: Any, platform_id: Optional[str]) -> None:
    """Record the platform's branch of record once.

    A Floor resume of a failed run records no rework-budget reset: a resume
    continues the same build with the rounds it had left (REWORK_BUDGET as
    the owner set it). Only Start over -- a fresh workspace and ledger --
    begins with the full budget."""
    from app.factory.build.ledger import (
        PLATFORM_BRANCH_KEY,
        PLATFORM_ID_KEY,
        EventKind,
    )
    from app.factory.build.platform_identity import branch_of_record, is_platform_id

    if is_platform_id(platform_id):
        recorded = None
        for event in reversed(list(ledger.events())):
            recorded = (event.payload or {}).get(PLATFORM_ID_KEY)
            if recorded:
                break
        if recorded != platform_id:
            ledger.append(
                EventKind.NOTE,
                detail=f"PLATFORM {platform_id}: branch of record {branch_of_record(platform_id)}",
                payload={
                    PLATFORM_ID_KEY: platform_id,
                    PLATFORM_BRANCH_KEY: branch_of_record(platform_id),
                },
            )


def start_runner_build(
    blueprint: Any,
    output_dir: Path | str,
    *,
    blocks_root: Optional[Path] = None,
    cycle: Optional[str] = None,
    quota_account_id: Optional[str] = None,
    tenant_identity: Optional[str] = None,
    brief: str = "",
    attach_branch: Optional[str] = None,
    attach_sha: Optional[str] = None,
    attach_passed: Optional[Sequence[str]] = None,
    platform_id: Optional[str] = None,
    start_over: bool = False,
    intake_changed: bool = False,
) -> Dict[str, Any]:
    """Start a background runner build and return immediately.

    ``platform_id``: the platform this build belongs to. Its branch of record,
    ``build/<platform_id>``, is recorded in the ledger once; the runner pushes
    every passed phase to it.

    ``start_over``: the ONLY way a failed run moves to a fresh workspace -- the
    typed Floor action "Start over", which has already tagged the old head.
    Without it a failed run is re-entered in place, never replaced.

    ``attach_branch``/``attach_sha``: the workspace is a checkout of that
    cerebrum-builds branch (branch_attach.attach). A new ledger records
    COLLECTOR/CLONER/WRITER as passed on the branch's evidence, so the run
    starts at the first phase the branch does not prove done.

    Returns the same keys the template path returns (``output_dir``,
    ``product_id``, ``inputs_hash``) so callers and stored session state do
    not need a second shape, plus ``build`` carrying the live status. The
    hash is the BLUEPRINT hash: the output tree does not exist yet, and
    keying on it -- as ``ProductGenerator.inputs_hash`` does -- would make
    resume impossible.
    """
    from app.factory.build.coder_session import (
        CodeCliUnavailable,
        OWNER_GATED_CLI_LOG,
        raise_if_cli_session_unready,
    )
    from app.factory.build.ledger import BuildLedger
    from app.factory.build.runner import blueprint_hash, frozen_blueprint

    resolved = (cycle or os.getenv("FACTORY_BUILD_SUITE") or "code").strip().lower()
    if resolved not in {"code", "pilot"}:
        resolved = "code"

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    # The build's identity is fixed HERE, once: the approved blueprint is
    # frozen (a deep copy the caller cannot reach) and hashed, and that one
    # value opens the ledger and travels into the thread. The runner never
    # re-hashes, so nothing done to any blueprint object after this point
    # can make the build disagree with its own ledger.
    blueprint = frozen_blueprint(blueprint)
    inputs_hash = blueprint_hash(blueprint)

    # Open the run record BEFORE the thread starts. Otherwise a client that
    # polls immediately reads "unknown" -- indistinguishable from "no build
    # was ever started" -- for however long the thread takes to reach its
    # first ledger write. The runner sees an existing ledger with a matching
    # inputs_hash and resumes into it rather than starting a second run.
    ledger = BuildLedger(_ledger_path(out))
    fresh_workspace = False
    fresh_reason = ""
    had_ledger = ledger.exists()
    if ledger.exists():
        status = build_status(out)
        if status.get("state") == "building":
            from app.factory.build.n3_store_gate import (
                handoff_awaiting_n3,
                n3_ingest_live,
                start_n3_ingest_job,
            )

            if handoff_awaiting_n3(out):
                if n3_ingest_live(out):
                    logger.info(
                        "refusing second N3 ingest; store-gate waiter already live at %s",
                        out,
                    )
                    return {
                        "engine": RUNNER,
                        "output_dir": str(out),
                        "product_id": getattr(blueprint, "product_id", "unknown"),
                        "inputs_hash": inputs_hash,
                        "build": status,
                        "cycle": resolved,
                        "already_running": True,
                        "n3_waiting": True,
                    }
                started = start_n3_ingest_job(out)
                logger.info(
                    "HANDOFF_TO_N3 at %s; %s store-gate ingest (no WRITER / BA)",
                    out,
                    "started" if started else "attached to",
                )
                return {
                    "engine": RUNNER,
                    "output_dir": str(out),
                    "product_id": getattr(blueprint, "product_id", "unknown"),
                    "inputs_hash": inputs_hash,
                    "build": build_status(out, blueprint=blueprint),
                    "cycle": resolved,
                    "already_running": not started,
                    "n3_waiting": True,
                    "n3_ingest": True,
                }
            logger.info("refusing second runner start; build already in progress at %s", out)
            return {
                "engine": RUNNER,
                "output_dir": str(out),
                "product_id": getattr(blueprint, "product_id", "unknown"),
                "inputs_hash": inputs_hash,
                "build": status,
                "cycle": resolved,
                "already_running": True,
            }
        if (
            status.get("state") == "stalled"
            and status.get("honesty") == FACTORY_CODE_CLI_ORPHANED
        ):
            logger.info(
                "resuming FACTORY_CODE_CLI_ORPHANED ledger at %s "
                "(no live WRITER / FACTORY_CODE_CLI worker)",
                out,
            )
        if status.get("state") == "failed" and not start_over:
            if intake_changed and ledger.inputs_hash() not in (None, inputs_hash):
                # Continue with new answers: the intake (locale, level,
                # vertical) is part of the frozen blueprint, so the inputs
                # changed. Same platform, same branch, same workspace -- the
                # run is REBASED onto the new inputs and every phase runs
                # again on the kept tree (the writer reworks what is there).
                # Mixing inputs without this typed rebase is still refused.
                ledger.rebase_inputs(
                    inputs_hash, reason="intake changed on Continue with new answers"
                )
        if status.get("state") == "failed":
            # G2: re-enter the SAME workspace when it is intact and its
            # ledger shows COLLECTOR/CLONER/WRITER passed; fresh only when it
            # is not, and the new ledger says why.
            from app.factory.build.ledger import EventKind

            phase, why = reattach_point(out)
            if phase is not None:
                ledger.append(
                    EventKind.NOTE,
                    detail=f"RESUMED workspace={out.name} phase={phase}",
                    payload={"resumed": True, "workspace": out.name, "phase": phase},
                )
                logger.info("re-entering failed run at %s, phase %s", out, phase)
            elif not start_over:
                # One platform = one workspace of record: a failed run is
                # resumed where it stopped, never silently replaced. A fresh
                # workspace exists only on the typed "Start over".
                ledger.append(
                    EventKind.NOTE,
                    detail=f"RESUMED workspace={out.name} phase=resume_point ({why})",
                    payload={"resumed": True, "workspace": out.name, "phase": None, "why": why},
                )
                logger.info("re-entering failed run at %s in place (%s)", out, why)
            else:
                prior = out.name
                fresh = next_fresh_output(out)
                logger.info(
                    "terminal ledger at %s not re-enterable (%s); fresh workspace at %s",
                    out,
                    why,
                    fresh,
                )
                out = fresh
                out.mkdir(parents=True, exist_ok=True)
                ledger = BuildLedger(_ledger_path(out))
                fresh_workspace = True
                fresh_reason = f"FRESH: prior workspace {prior} not re-enterable: {why}"

    # F-2: a build whose exam proofs cannot be kept is refused at the door
    # with the plain reason, not later at its vendored-lock gate.
    from app.factory.build.coder_session import BuildsUnavailable
    from app.factory.build.store_override import builds_unavailable_reason

    builds_refused = builds_unavailable_reason(out.parent)
    if builds_refused is not None:
        logger.error("build refused at start: %s", builds_refused)
        raise BuildsUnavailable(builds_refused)

    try:
        raise_if_cli_session_unready()
    except CodeCliUnavailable:
        logger.error("%s", OWNER_GATED_CLI_LOG)
        raise

    if not ledger.exists():
        ledger.start_run(
            product_id=getattr(blueprint, "product_id", "unknown"),
            inputs_hash=inputs_hash,
        )
        if fresh_workspace and fresh_reason:
            from app.factory.build.ledger import EventKind

            ledger.append(EventKind.NOTE, detail=fresh_reason, payload={"fresh": True})

    _record_platform(ledger, platform_id)

    if attach_branch:
        from app.factory.build.authority import BuildRole
        from app.factory.build.branch_attach import DERIVED_DONE
        from app.factory.build.ledger import EventKind

        short = (attach_sha or "")[:7]
        if not had_ledger:
            for name in tuple(attach_passed or DERIVED_DONE):
                ledger.append(
                    EventKind.GATE_PASSED,
                    role=BuildRole[name],
                    detail=f"derived from {attach_branch}@{short}: the branch tree proves it done",
                    payload={"gate": "branch_evidence", "attached_branch": attach_branch, "sha": attach_sha},
                )
        point = ledger.resume_point()
        phase = point.value if point is not None else "done"
        ledger.append(
            EventKind.NOTE,
            detail=f"RESUMED branch={attach_branch} sha={short} phase={phase}",
            payload={"resumed": True, "attached_branch": attach_branch, "sha": attach_sha, "phase": phase},
        )

    _write_quota_marker(out, quota_account_id)
    # F-U1a: a build starts running. A Stop left by an earlier build of this
    # workspace belongs to that build, never to this one.
    from app.factory.build.coder_session_status import CONTROL_RUN, write_control

    write_control(out, CONTROL_RUN)

    # Phase 1 tenant isolation: bind the store handle from the
    # authenticated identity BEFORE the thread starts. An unauthenticated
    # build gets None and the worker refuses (no_authenticated_tenant)
    # instead of running unbound. The account id is the only accepted
    # identity — a caller without one (admin/master-key) is refused by
    # name, never bound to a session-name digest the caller could choose.
    from app.factory.build.tenant_bind import bind_tenant_store

    identity = tenant_identity if tenant_identity is not None else quota_account_id
    # Worker-side tenant check (independent of the route guard): the store we
    # isolate on (``identity``) must be the account the build is owned/metered
    # under (``quota_account_id``). A job whose bound tenant is not that owner
    # is refused (TenantMismatchError) instead of isolating one tenant's store
    # under another's build. When the build carries only a single identity
    # (resume: tenant_identity is None and quota_account_id drives both) the
    # two agree by construction and the check is a no-op.
    tenant_store = bind_tenant_store(identity, owner=quota_account_id)

    thread = threading.Thread(
        target=_run,
        args=(
            blueprint,
            out,
            Path(blocks_root) if blocks_root else None,
            resolved,
            tenant_store,
            str(brief or "").strip(),
            inputs_hash,
        ),
        name=runner_thread_name(out),
        daemon=True,
    )
    register_runner_thread(out, thread)
    thread.start()
    logger.info("runner build started for %s at %s", inputs_hash[:12], out)

    return {
        "engine": RUNNER,
        "output_dir": str(out),
        "product_id": getattr(blueprint, "product_id", "unknown"),
        "inputs_hash": inputs_hash,
        "build": build_status(out),
        "cycle": resolved,
        "already_running": False,
        "fresh_workspace": fresh_workspace,
    }
