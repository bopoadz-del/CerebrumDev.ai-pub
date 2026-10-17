"""Build ledger — the durable, resumable record of one manufacturing run.

A two-hour build cannot be a function call whose only output is its return
value. It has to survive an interrupt, be auditable afterwards, and answer
"what did each role actually do" without anyone having to trust a summary.

The ledger is append-only JSONL for that reason: a crash mid-phase leaves
every prior line intact and readable, where a rewritten JSON document would
leave a truncated file and no history. Nothing here mutates or deletes a
past event -- a phase that is re-run appends a new event, and
:meth:`BuildLedger.completed_roles` reads the latest verdict per role.

The ledger is also the registrar's source of truth. ``CLONE`` events record
which block landed in which platform at which commit, so the Store Manager
can answer "what did this platform take from the store, and has any of it
gone stale against store head" by reading ledgers rather than by re-scanning
delivered artifacts.

Only the orchestrator writes here, and only through :meth:`BuildLedger.append`
(which always assigns ``seq``). Roles do not -- a role that can edit the
record of its own gate is the same hole that
:mod:`app.factory.build.authority` closes for source files. FACTORY_CODE_CLI
runs inside the workspace and has been seen appending seq-less NOTE objects
as raw JSONL (CEREBRUMDEV-BACKEND-B). Readers quarantine that shape;
:meth:`BuildLedger.protect` makes the file owner-readonly during a role.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional, Set

logger = logging.getLogger(__name__)

_FILE_LOCKS: Dict[str, threading.Lock] = {}
_FILE_LOCKS_GUARD = threading.Lock()


def _thread_lock_for(path: Path) -> threading.Lock:
    key = str(path)
    with _FILE_LOCKS_GUARD:
        lock = _FILE_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _FILE_LOCKS[key] = lock
        return lock


@contextmanager
def _exclusive_ledger(path: Path):
    """Process + inter-process lock around a ledger append."""
    lock_path = Path(str(path) + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    thread_lock = _thread_lock_for(path)
    with thread_lock:
        with lock_path.open("a+", encoding="utf-8") as lock_fh:
            flocked = False
            try:
                import fcntl

                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
                flocked = True
            except ImportError:
                pass
            try:
                yield
            finally:
                if flocked:
                    import fcntl

                    fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)

from app.factory.build.authority import BUILD_PHASES, BuildRole

LEDGER_SCHEMA = "build_ledger.v1"


#: NOTE payload keys recording a platform's identity and its branch of record.
PLATFORM_ID_KEY = "platform_id"
#: NOTE payload key of a typed inputs rebase ("Continue with new answers"):
#: the run continues on the same workspace from new inputs, every phase anew.
INPUTS_REBASED_KEY = "inputs_rebased"
PLATFORM_BRANCH_KEY = "platform_branch"


class EventKind(str, Enum):
    RUN_STARTED = "RUN_STARTED"
    PHASE_STARTED = "PHASE_STARTED"
    GATE_PASSED = "GATE_PASSED"
    GATE_FAILED = "GATE_FAILED"
    PHASE_ABORTED = "PHASE_ABORTED"
    #: A writer<->tester round trip. Recorded so a run that converged after
    #: eleven attempts does not read like one that passed first try.
    REWORK = "REWORK"
    CLONE = "CLONE"
    NOTE = "NOTE"
    #: Reopens TESTER + STORE_MANAGER after a code-phase SUCCESS so the same
    #: workspace can run the Store-green / ``pytest -m pilot`` cycle. Does
    #: not wipe WRITER artifacts or start a second product.
    PILOT_OPENED = "PILOT_OPENED"
    #: Terminal verdicts. Exactly one of these ends a finished run, so the
    #: outcome is readable from the ledger alone without re-deriving it from
    #: phase events -- and a run that stopped without one is, correctly, not
    #: a run that succeeded.
    RUN_SUCCEEDED = "RUN_SUCCEEDED"
    RUN_FAILED = "RUN_FAILED"


#: Kinds that end a phase. The last terminal event for a role is its verdict.
TERMINAL_KINDS = frozenset(
    {EventKind.GATE_PASSED, EventKind.GATE_FAILED, EventKind.PHASE_ABORTED}
)


class LedgerError(RuntimeError):
    """The ledger on disk cannot be trusted for the run being attempted."""


#: Honesty class when an external writer (FACTORY_CODE_CLI in the workspace)
#: appended a complete NOTE object without going through :meth:`BuildLedger.append`.
#: Those lines are not verdicts. Quarantining them keeps ``_next_seq`` / chat /
#: status alive; inventing ``pilot_ready`` from a torn ledger is still forbidden.
LEDGER_EXTERNAL_NOTE_QUARANTINED = "LEDGER_EXTERNAL_NOTE_QUARANTINED"
LEDGER_UNREADABLE = "LEDGER_UNREADABLE"
QUARANTINE_PAYLOAD_KEY = "ledger_quarantine"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _coerce_seq(raw: Mapping[str, Any]) -> Optional[int]:
    """Return a positive ledger seq, or None when the field is absent/unusable."""
    if "seq" not in raw:
        return None
    value = raw["seq"]
    if value is None or value == "":
        return None
    try:
        seq = int(value)
    except (TypeError, ValueError):
        return None
    return seq if seq > 0 else None


def _is_external_note(raw: Any) -> bool:
    """True for a complete JSON object that is a NOTE, not a phase verdict."""
    return isinstance(raw, Mapping) and str(raw.get("kind") or "") == EventKind.NOTE.value


@dataclass(frozen=True)
class BuildEvent:
    seq: int
    ts: str
    kind: EventKind
    role: Optional[BuildRole] = None
    detail: str = ""
    payload: Mapping[str, Any] = field(default_factory=dict)

    def to_json(self) -> Dict[str, Any]:
        return {
            "seq": self.seq,
            "ts": self.ts,
            "kind": self.kind.value,
            "role": self.role.value if self.role else None,
            "detail": self.detail,
            "payload": dict(self.payload),
        }

    @staticmethod
    def from_json(raw: Mapping[str, Any]) -> "BuildEvent":
        role = raw.get("role")
        return BuildEvent(
            seq=int(raw["seq"]),
            ts=str(raw.get("ts", "")),
            kind=EventKind(raw["kind"]),
            role=BuildRole(role) if role else None,
            detail=str(raw.get("detail", "")),
            payload=dict(raw.get("payload") or {}),
        )


class BuildLedger:
    """Append-only run record at *path*.

    ``inputs_hash`` pins the run to the blueprint that started it. Resuming
    against a different blueprint is refused rather than silently continuing
    a build whose earlier phases were made from different inputs.
    """

    def __init__(
        self,
        path: Path | str,
        *,
        clock: Callable[[], str] = _utc_now,
    ) -> None:
        self.path = Path(path)
        self._clock = clock
        self._protect_depth = 0

    # -- reading ---------------------------------------------------------

    def exists(self) -> bool:
        return self.path.is_file()

    def events(self) -> List[BuildEvent]:
        if not self.path.is_file():
            return []
        out: List[BuildEvent] = []
        with self.path.open("r", encoding="utf-8") as fh:
            physical = [
                (lineno, line.strip())
                for lineno, line in enumerate(fh, start=1)
                if line.strip()
            ]
        last_lineno = physical[-1][0] if physical else 0
        for lineno, line in physical:
            try:
                raw = json.loads(line)
            except ValueError as exc:
                # A half-written *final* line is the expected shape of a
                # crash mid-append. Anything earlier is real corruption.
                if lineno == last_lineno:
                    logger.warning(
                        "half-written final ledger line at %s:%s — skipping",
                        self.path,
                        lineno,
                    )
                    continue
                raise LedgerError(
                    f"{self.path}:{lineno} is not a readable ledger event: {exc}"
                ) from exc
            try:
                event = self._event_from_raw(raw, lineno=lineno)
            except (ValueError, KeyError, TypeError) as exc:
                raise LedgerError(
                    f"{self.path}:{lineno} is not a readable ledger event: {exc}"
                ) from exc
            if event is not None:
                out.append(event)
        return out

    def _event_from_raw(self, raw: Any, *, lineno: int) -> Optional[BuildEvent]:
        if not isinstance(raw, Mapping):
            raise TypeError(f"expected object, got {type(raw).__name__}")
        seq = _coerce_seq(raw)
        if seq is None:
            if _is_external_note(raw):
                return self._quarantine_external_note(raw, lineno=lineno)
            raise KeyError("seq")
        return BuildEvent.from_json({**dict(raw), "seq": seq})

    def _quarantine_external_note(
        self, raw: Mapping[str, Any], *, lineno: int
    ) -> BuildEvent:
        """Keep a seq-less NOTE visible without bricking ``_next_seq``.

        Live shape (CEREBRUMDEV-BACKEND-B / sess_69f28c0d8bc540e9:4561): a
        complete JSON object with ``ts``, ``role=WRITER``, ``kind=NOTE``,
        ``detail`` (rework fix text), ``payload.source=coder CLI`` — and no
        ``seq``. FACTORY_CODE_CLI wrote it as raw JSONL into the workspace
        ledger. ``seq=0`` is reserved for quarantined lines so
        :meth:`_next_seq` stays monotonic on factory-assigned ids.
        """
        payload = dict(raw.get("payload") or {})
        payload[QUARANTINE_PAYLOAD_KEY] = LEDGER_EXTERNAL_NOTE_QUARANTINED
        payload["ledger_line"] = lineno
        role_raw = raw.get("role")
        try:
            role = BuildRole(role_raw) if role_raw else None
        except ValueError:
            role = None
        logger.warning(
            "%s: %s:%s seq-less NOTE quarantined (source=%s stage=%s) — run continues",
            LEDGER_EXTERNAL_NOTE_QUARANTINED,
            self.path,
            lineno,
            payload.get("source"),
            payload.get("stage"),
        )
        return BuildEvent(
            seq=0,
            ts=str(raw.get("ts", "")),
            kind=EventKind.NOTE,
            role=role,
            detail=str(raw.get("detail", "")),
            payload=payload,
        )

    def _next_seq(self) -> int:
        seqs = [event.seq for event in self.events() if event.seq > 0]
        return (max(seqs) + 1) if seqs else 1

    def quarantined_notes(self) -> int:
        return sum(
            1
            for event in self.events()
            if (event.payload or {}).get(QUARANTINE_PAYLOAD_KEY)
            == LEDGER_EXTERNAL_NOTE_QUARANTINED
        )

    # -- writing ---------------------------------------------------------

    def append(
        self,
        kind: EventKind,
        *,
        role: Optional[BuildRole | str] = None,
        detail: str = "",
        payload: Optional[Mapping[str, Any]] = None,
    ) -> BuildEvent:
        event = BuildEvent(
            seq=self._next_seq(),
            ts=self._clock(),
            kind=EventKind(kind),
            role=BuildRole(role) if role else None,
            detail=detail,
            payload=dict(payload or {}),
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event.to_json(), sort_keys=True) + "\n"
        # One write plus fsync per event, under a file lock so two workers
        # cannot interleave JSONL lines. While :meth:`protect` is active the
        # file is owner-readonly except for this locked window, so a CLI
        # subprocess cannot append raw JSONL (the CEREBRUMDEV-BACKEND-B path).
        with _exclusive_ledger(self.path):
            # Always lift leftover 0444 from a killed protect() window
            # (Render deploy mid-WRITER). protect() still re-applies
            # owner-readonly in the finally below.
            self._chmod_writable()
            try:
                self._write_line(line)
            except PermissionError:
                self._chmod_writable()
                try:
                    self._write_line(line)
                except PermissionError as exc:
                    raise LedgerError(
                        f"ledger not writable at {self.path}: {exc}"
                    ) from exc
            finally:
                if self._protect_depth:
                    self._chmod_readonly()
        return event

    def _write_line(self, line: str) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())

    def _chmod_readonly(self) -> None:
        if not self.path.is_file():
            return
        try:
            os.chmod(self.path, 0o444)
        except OSError as exc:
            logger.warning("could not protect ledger %s: %s", self.path, exc)

    def _chmod_writable(self) -> None:
        if not self.path.is_file():
            return
        try:
            os.chmod(self.path, 0o644)
        except OSError as exc:
            logger.warning("could not unprotect ledger %s: %s", self.path, exc)

    @contextmanager
    def protect(self):
        """Owner-readonly while a role (and its CLI) is running.

        Factory :meth:`append` lifts the bit under the file lock. Raw
        ``open(..., 'a')`` from FACTORY_CODE_CLI is PermissionError.
        """
        self._protect_depth += 1
        self._chmod_readonly()
        try:
            yield self
        finally:
            self._protect_depth -= 1
            if self._protect_depth <= 0:
                self._chmod_writable()

    def start_run(self, *, product_id: str, inputs_hash: str) -> BuildEvent:
        return self.append(
            EventKind.RUN_STARTED,
            detail=f"build of {product_id}",
            payload={
                "schema": LEDGER_SCHEMA,
                "product_id": product_id,
                "inputs_hash": inputs_hash,
            },
        )

    def rebase_inputs(self, inputs_hash: str, *, reason: str) -> BuildEvent:
        """Continue the SAME run from new inputs (the typed "Continue with new
        answers"). Append-only: the old verdicts stay in the record, but every
        phase must pass again against the new inputs, and the resume guard
        reads the new hash from here on."""
        return self.append(
            EventKind.NOTE,
            detail=f"INPUTS REBASED: {reason}",
            payload={
                INPUTS_REBASED_KEY: True,
                "inputs_hash": inputs_hash,
                "previous_inputs_hash": self.inputs_hash(),
                "reason": reason,
            },
        )

    def open_pilot_cycle(
        self,
        *,
        reason: str = "code-phase SUCCESS; opening Store-green cycle",
        reopen_writer: bool = False,
    ) -> BuildEvent:
        """Reopen TESTER + STORE_MANAGER on an existing workspace.

        Append-only: COLLECTOR/CLONER/WRITER verdicts stay unless
        ``reopen_writer`` is set (acceptance harness / route auth never
        stamped — STORE cannot measure k/12 without WRITER). A later
        ``RUN_SUCCEEDED`` with ``cycle=pilot`` is what ``pilot_ready`` reads.
        """
        return self.append(
            EventKind.PILOT_OPENED,
            detail=reason,
            payload={"cycle": "pilot", "reopen_writer": bool(reopen_writer)},
        )

    def record_clone(
        self,
        *,
        block_id: str,
        source_commit: str,
        store_repo: str,
        vendored_path: str,
    ) -> BuildEvent:
        """Register one block taken from the store into this platform."""
        return self.append(
            EventKind.CLONE,
            role=BuildRole.CLONER,
            detail=f"cloned {block_id}@{source_commit[:12]}",
            payload={
                "block_id": block_id,
                "source_commit": source_commit,
                "store_repo": store_repo,
                "vendored_path": vendored_path,
            },
        )

    # -- derived state ---------------------------------------------------

    def inputs_hash(self) -> Optional[str]:
        """The hash the run is pinned to: RUN_STARTED's, or the latest typed
        rebase's (``rebase_inputs``)."""
        current: Optional[str] = None
        for event in self.events():
            payload = event.payload or {}
            if event.kind is EventKind.RUN_STARTED and current is None:
                current = str(payload.get("inputs_hash") or "") or None
            elif event.kind is EventKind.NOTE and payload.get(INPUTS_REBASED_KEY):
                current = str(payload.get("inputs_hash") or "") or current
        return current

    def completed_roles(self) -> Set[BuildRole]:
        """Roles whose most recent *completed* attempt was a pass.

        Latest-verdict-wins, so a role that failed, was reworked and then
        passed counts as complete, and one that passed and was later aborted
        does not.

        A role whose last event is ``PHASE_STARTED`` is **running, not
        complete**, even if an earlier attempt passed. That distinction is the
        difference between resuming correctly and resuming onto rubble: a
        process killed mid-WRITER leaves files from two different attempts,
        and the agent picks different entity names each call, so the halves do
        not compose. Reading the stale GATE_PASSED would resume at TESTER and
        test a torn workspace.
        """
        state: Dict[BuildRole, EventKind] = {}
        for event in self.events():
            if event.kind is EventKind.PILOT_OPENED:
                # Code-phase TESTER used ``not pilot``; STORE_MANAGER applied
                # no store op. Both must run again. WRITER stays complete
                # unless the harness / route-auth surface was never stamped.
                state.pop(BuildRole.TESTER, None)
                state.pop(BuildRole.STORE_MANAGER, None)
                if (event.payload or {}).get("reopen_writer"):
                    state.pop(BuildRole.WRITER, None)
                continue
            if event.kind is EventKind.NOTE and (event.payload or {}).get(INPUTS_REBASED_KEY):
                # New inputs: nothing passed against them yet.
                state.clear()
                continue
            if event.kind is EventKind.REWORK and (event.payload or {}).get("reopen"):
                # A rework that re-opens a finished run (a product-owned
                # Store-gate failure sends the WRITER back): every phase it
                # names runs again.
                for value in (event.payload or {}).get("reopen") or ():
                    try:
                        state.pop(BuildRole(value), None)
                    except ValueError:
                        pass
                continue
            if not event.role:
                continue
            if event.kind is EventKind.PHASE_STARTED or event.kind in TERMINAL_KINDS:
                state[event.role] = event.kind
        return {r for r, k in state.items() if k is EventKind.GATE_PASSED}

    def interrupted_role(self) -> Optional[BuildRole]:
        """The role that started and never finished, if the run was killed."""
        state: Dict[BuildRole, EventKind] = {}
        for event in self.events():
            if not event.role:
                continue
            if event.kind is EventKind.PHASE_STARTED or event.kind in TERMINAL_KINDS:
                state[event.role] = event.kind
        for role, kind in state.items():
            if kind is EventKind.PHASE_STARTED:
                return role
        return None

    def resume_point(self) -> Optional[BuildRole]:
        """The first phase not yet passed, or None when the run is finished."""
        done = self.completed_roles()
        for phase in BUILD_PHASES:
            if phase not in done:
                return phase
        return None

    def assert_resumable(self, *, inputs_hash: str) -> None:
        """Refuse to continue a run whose inputs changed underneath it."""
        recorded = self.inputs_hash()
        if recorded is None:
            return
        if recorded != inputs_hash:
            raise LedgerError(
                "cannot resume: ledger was started from inputs "
                f"{recorded[:12]} but this run supplies {inputs_hash[:12]} — "
                "start a fresh build rather than mixing them"
            )

    def clones(self) -> List[Dict[str, Any]]:
        """Every block this platform took from the store, latest per block."""
        latest: Dict[str, Dict[str, Any]] = {}
        for event in self.events():
            if event.kind is EventKind.CLONE:
                bid = str(event.payload.get("block_id") or "")
                if bid:
                    latest[bid] = {**dict(event.payload), "ts": event.ts}
        return [latest[k] for k in sorted(latest)]

    def terminal_event(self) -> Optional[BuildEvent]:
        """The run's recorded verdict, or None if it never finished.

        None is the honest answer for a killed run: absence of a verdict is
        not success, and callers must not infer one from "no failures seen".
        A ``PILOT_OPENED`` -- or a REWORK carrying ``reopen`` -- after the
        last terminal reopens the run.
        """
        last: Optional[BuildEvent] = None
        for event in self.events():
            if event.kind in (EventKind.RUN_SUCCEEDED, EventKind.RUN_FAILED):
                last = event
            elif event.kind is EventKind.PILOT_OPENED:
                last = None
            elif event.kind is EventKind.NOTE and (event.payload or {}).get(INPUTS_REBASED_KEY):
                last = None
            elif event.kind is EventKind.REWORK and (event.payload or {}).get("reopen"):
                last = None
        return last

    def reopening_rework(self) -> Optional[BuildEvent]:
        """The REWORK that re-opened a finished run, while nothing has run
        since it; None otherwise."""
        events = self.events()
        if events and events[-1].kind is EventKind.REWORK and (
            events[-1].payload or {}
        ).get("reopen"):
            return events[-1]
        return None

    def succeeded(self) -> bool:
        event = self.terminal_event()
        return event is not None and event.kind is EventKind.RUN_SUCCEEDED

    def code_phase_succeeded(self) -> bool:
        """True once any RUN_SUCCEEDED was recorded (code-phase 5/5 counts)."""
        return any(e.kind is EventKind.RUN_SUCCEEDED for e in self.events())

    def pilot_cycle_open(self) -> bool:
        """True when a PILOT_OPENED is the latest cycle marker (no later SUCCESS)."""
        last_pilot = last_success = None
        for event in self.events():
            if event.kind is EventKind.PILOT_OPENED:
                last_pilot = event
            if event.kind is EventKind.RUN_SUCCEEDED:
                last_success = event
        return last_pilot is not None and (
            last_success is None or last_pilot.seq > last_success.seq
        )

    def pilot_ready(self) -> bool:
        """True only after a SUCCESS that closed a pilot cycle."""
        event = self.terminal_event()
        if event is None or event.kind is not EventKind.RUN_SUCCEEDED:
            return False
        payload = event.payload or {}
        return payload.get("cycle") == "pilot" or bool(payload.get("pilot_ready"))

    def rework_counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for event in self.events():
            if event.kind is EventKind.REWORK and event.role:
                counts[event.role.value] = counts.get(event.role.value, 0) + 1
        return counts

    def summary(self) -> Dict[str, Any]:
        events = self.events()
        done = self.completed_roles()
        resume = self.resume_point()
        quarantined = self.quarantined_notes()
        return {
            "schema_version": LEDGER_SCHEMA,
            "path": str(self.path),
            "events": len(events),
            "started": events[0].ts if events else None,
            "last_event": events[-1].ts if events else None,
            "inputs_hash": self.inputs_hash(),
            "completed_roles": [p.value for p in BUILD_PHASES if p in done],
            "resume_point": resume.value if resume else None,
            "complete": resume is None,
            "rework": self.rework_counts(),
            "clones": self.clones(),
            "quarantined_notes": quarantined,
            "outcome": (
                self.terminal_event().kind.value if self.terminal_event() else None
            ),
        }


def iter_ledgers(root: Path | str, *, filename: str = "build_ledger.jsonl") -> Iterator[BuildLedger]:
    """Every build ledger under *root* — the registrar's scan entry point."""
    base = Path(root)
    if not base.is_dir():
        return
    for path in sorted(base.rglob(filename)):
        yield BuildLedger(path)
