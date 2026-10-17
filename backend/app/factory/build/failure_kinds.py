"""Typed kinds for build failures, decided by what failed -- never by words.

A failure's kind travels beside its message: ``coder_failure_kinds[key]`` in
the run state and provenance, ``payload["failure_kind"]`` on a ledger event.
Readers (budget_inspect) count kinds; they never search the message text,
which is prose for a person and changes whenever someone rewords it.
"""

from __future__ import annotations

import subprocess
from typing import Any, Iterator, MutableMapping, Optional

#: The work ran out of wall clock: a watchdog, a deadline or a killed session.
TIMEOUT = "timeout"
#: A coder call that was never made: the build budget could not fit it.
BUDGET_SKIPPED = "budget_skipped"


def _chain(exc: Optional[BaseException]) -> Iterator[BaseException]:
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        yield exc
        exc = exc.__cause__ or exc.__context__


def failure_kind(exc: Optional[BaseException]) -> str:
    """TIMEOUT when the exception -- or anything it was raised from -- is a
    timeout by TYPE; otherwise "" (no kind claimed)."""
    from app.factory.coder import CoderTimeout
    from app.workbench.sandbox import SandboxTimeout

    timeout_types = (TimeoutError, subprocess.TimeoutExpired, CoderTimeout, SandboxTimeout)
    return TIMEOUT if any(isinstance(e, timeout_types) for e in _chain(exc)) else ""


def record_failure_kind(state: MutableMapping[str, Any], key: str, kind: str) -> None:
    """Store ``kind`` for the failure recorded under ``coder_failures[key]``."""
    if kind:
        state.setdefault("coder_failure_kinds", {})[key] = kind
