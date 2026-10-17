"""Role runner types.

Extracted from ``roles.py`` so the god file can re-export without behavior change.
Existing imports (``from app.factory.build.roles import RoleContext``) keep working.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from app.factory.build.authority import BuildRole
from app.factory.build.workspace import RoleWorkspace


@dataclass
class RoleContext:
    """Everything a role is given. Nothing is reachable except through this."""

    role: BuildRole
    workspace: RoleWorkspace
    blueprint: Any
    plan: Any
    blocks_root: Optional[Path] = None
    #: Factory store pin. When set (or loaded from blocks.lock.json), a
    #: store-sourced consumed block that is unlocked or hash-mismatched
    #: is a hard failure. Tests with synthetic stores pass a generated lock.
    blocks_lock: Optional[Dict[str, Any]] = None
    #: Findings from the gate that sent this role back round. The WRITER's
    #: work list on a rework pass; empty on a first pass.
    work_list: Sequence[str] = ()
    #: Carried forward between phases (gaps from COLLECTOR, blocks from CLONER).
    state: Dict[str, Any] = field(default_factory=dict)
    #: Monotonic deadline for this role pass, set by the runner to the
    #: earlier of the whole-build wall and the per-phase wall (25 min on
    #: a code-only pass; staged 30→45 min on Store-green / auto-pilot).
    #: The coder yields once too little remains for one call. A leftover
    #: high wall is honoured; the default path is stop-and-inspect.
    deadline: Optional[float] = None
    #: Live deadline box shared with the runner so a mid-phase inspect
    #: ramp updates ``coder_time_left`` without restarting the role.
    deadline_box: Optional[Dict[str, Any]] = None
    #: Optional progress sink, wired by the runner to a ledger NOTE. Roles
    #: stay ledger-unaware; without it ``note()`` is a no-op, so a role is
    #: testable without a ledger. Exists because a WRITER pass that takes
    #: twenty minutes of agent calls otherwise reports nothing at all: the
    #: ledger only records phase boundaries, so a customer watching a live
    #: build saw a frozen "2/5" and could not tell work from a hang.
    progress: Optional[Any] = None

    def coder_time_left(self) -> Optional[float]:
        """Seconds of build budget remaining, or None when unbounded."""
        deadline = self.deadline
        clock = None
        if self.deadline_box is not None:
            boxed = self.deadline_box.get("at")
            if boxed is not None:
                deadline = boxed
            boxed_clock = self.deadline_box.get("clock")
            if callable(boxed_clock):
                clock = boxed_clock
        if deadline is None:
            return None
        if clock is None:
            import time as _time

            clock = _time.monotonic
        return deadline - clock()

    def note(self, detail: str, **payload: Any) -> None:
        if self.progress is None:
            return
        try:
            self.progress(detail, payload)
        except Exception:  # noqa: BLE001 -- telemetry must never fail a build
            pass


@dataclass
class RoleResult:
    ok: bool
    detail: str = ""
    #: F1: named reason token (writer_no_output, codewhale_worker_failed,
    #: worker_timed_out, ...) — structural, never only a detail string.
    reason: str = ""
    #: F1: where the failure lives (role name or gate id).
    location: str = ""
    #: Merged into the shared state and into the next GateContext.
    gaps: tuple = ()
    vendored_blocks: tuple = ()
    notes: Dict[str, Any] = field(default_factory=dict)


class RoleError(RuntimeError):
    """A role could not do its job. Never swallowed into a partial success."""

    def __init__(self, detail: str, *, reason: str = "", location: str = "") -> None:
        super().__init__(detail)
        self.detail = detail
        self.reason = reason
        self.location = location
