"""FAILED(<gate>, <check>, <finding>) -- the one reader of a failed build's verdict.

A failed build is still a product: it exports on the explicit ask, and its
zip MANIFEST and the Floor's technical detail name the SAME three facts. They
are read here, once, from the run's terminal ledger event. What the Floor
SAYS about a stop -- its class and one sentence each for what happened and
what continuing does -- is chosen here too, from typed fields only.

The runner rule (rule_decision.py) records a STOP as the terminal RUN_FAILED
payload's ``decision`` {gate, check, finding, class, ...}; it is read through
``rule_decision.stop_record``. A ledger written before that record existed
still carries the failing phase, the gate's reason and its findings -- the
same facts under older names, read as a fallback, never parsed out of prose.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

#: The terminal payload keys the runner rule writes.
GATE_KEY = "gate"
CHECK_KEY = "check"
FINDING_KEY = "finding"

#: The CLOSED set of ways a build stops. The Floor's sentence is chosen by
#: class, read from typed fields -- never by reason code (an open set that
#: names the outermost wrapper, not the cause).
OUT_OF_TIME = "out_of_time"
CHECK_REFUSED = "check_refused"
ROUNDS_SPENT = "rounds_spent"
COULD_NOT_RUN = "could_not_run"
STOPPED_BY_OWNER = "stopped_by_owner"
UNEXPECTED = "unexpected"

_AGAIN = "steps that already passed are not repeated"
#: class -> (title, what happened, what continuing does). No stage id and no
#: reason code in any of them: the Floor puts its own stage label in front.
STOP_TEXT: Dict[str, Tuple[str, str, str]] = {
    OUT_OF_TIME: (
        "ran out of time",
        "The time allowance ended before this step finished{ran}. No check refused the work.",
        f"Continuing runs this step again with a new time allowance; {_AGAIN}, and the "
        "coder carries on from its saved files where it can.",
    ),
    CHECK_REFUSED: (
        "did not pass its check",
        "A check looked at the work and did not pass it{items}.",
        "Continuing runs this step again with what the check found, using the fix rounds "
        "this build has left.",
    ),
    ROUNDS_SPENT: (
        "used up its fix rounds",
        "A check still did not pass the work{items}, and this build has no fix rounds left.",
        "Continuing does not grant new fix rounds: the step runs again and stops again if "
        "the same check fails.",
    ),
    COULD_NOT_RUN: (
        "could not finish",
        "This step stopped before it finished. No check judged the work.",
        f"Continuing runs this step again; {_AGAIN}.",
    ),
    STOPPED_BY_OWNER: (
        "was stopped by you",
        "You pressed Stop. Nothing failed.",
        f"Continuing resumes the build at this step; {_AGAIN}.",
    ),
    UNEXPECTED: (
        "stopped unexpectedly",
        "The build stopped without a verdict. The cause is in the build record.",
        f"Continuing runs this step again; {_AGAIN}.",
    ),
}
#: What the failing event itself says when the run's outcome does not decide.
_ROW_CLASS = {"failed": CHECK_REFUSED, "aborted": COULD_NOT_RUN}


def _first(items: Optional[Sequence[Any]]) -> str:
    for item in items or ():
        text = str(item or "").strip()
        if text:
            return text
    return ""


def failure_triple(
    terminal_payload: Optional[Mapping[str, Any]],
    failure: Optional[Mapping[str, Any]] = None,
) -> Optional[Dict[str, str]]:
    """``{gate, check, finding}`` for a failed run, or None when nothing failed.

    ``terminal_payload`` is the RUN_FAILED event's payload; ``failure`` is the
    status's phase-trail failure entry (phase / reason / detail)."""
    from app.factory.build import rule_decision as rd

    payload = dict(terminal_payload or {})
    trail = dict(failure or {})
    stop = (payload.get(rd.DECISION_KEY) or {}) if isinstance(payload.get(rd.DECISION_KEY), dict) else {}
    if stop.get(rd.CLASS) == rd.STOP:
        return {
            "gate": str(stop.get(rd.GATE) or "unknown"),
            "check": str(stop.get(rd.CHECK) or "unknown"),
            "finding": str(stop.get(rd.FINDING) or ""),
        }
    gate = str(
        payload.get(GATE_KEY) or trail.get("phase") or payload.get("phase") or trail.get("location") or ""
    ).strip()
    check = str(payload.get(CHECK_KEY) or trail.get("reason") or payload.get("reason") or "").strip()
    finding = _without_own_code(
        check,
        str(
            payload.get(FINDING_KEY)
            or _first(payload.get("findings"))
            or trail.get("detail")
            or ""
        ).strip(),
    )
    if not (gate or check or finding):
        return None
    return {"gate": gate or "unknown", "check": check or "unknown", "finding": finding}


def _without_own_code(code: str, message: str) -> str:
    """``message`` without the code it is filed under: emitters prefix the
    reason token onto their prose, so code beside prose read ``<code>: <code>:``."""
    prefix = f"{code}:"
    while code and message.startswith(prefix):
        message = message[len(prefix):].lstrip()
    return message


def stop_class(
    terminal_payload: Optional[Mapping[str, Any]],
    failure: Optional[Mapping[str, Any]] = None,
) -> str:
    """Which of the six ways this build stopped -- from typed fields only.

    The terminal event's ``outcome`` decides first (the owner's Stop, the time
    budget, a lane violation, spent fix rounds); otherwise the failing event
    (the status's ``failure`` row): its typed ``failure_kind``, then its kind
    (``failed`` = a gate refused the work, ``aborted`` = it could not finish)."""
    from app.factory.build import failure_kinds
    from app.factory.build import rule_decision as rd
    from app.factory.build.runner import Outcome

    payload = dict(terminal_payload or {})
    row = dict(failure or {})
    outcome = str(payload.get("outcome") or "")
    if outcome == Outcome.FAILED_BUDGET_SPENT.value:
        # One outcome, two stops: the rule's STOP record means the fix rounds
        # ran out; without one it is the wall-clock budget that was spent.
        stop = payload.get(rd.DECISION_KEY)
        spent = isinstance(stop, dict) and stop.get(rd.CLASS) == rd.STOP
        return ROUNDS_SPENT if spent else OUT_OF_TIME
    by_outcome = {
        Outcome.STOPPED_BY_OWNER.value: STOPPED_BY_OWNER,
        Outcome.FAILED_TIME_BUDGET.value: OUT_OF_TIME,
        Outcome.FAILED_AUTHORITY.value: COULD_NOT_RUN,
    }.get(outcome)
    if by_outcome:
        return by_outcome
    if row.get("failure_kind") in (failure_kinds.TIMEOUT, failure_kinds.MODEL_CALL_TIMEOUT):
        return OUT_OF_TIME
    if outcome == Outcome.FAILED_GATE.value:
        return CHECK_REFUSED
    fallback = COULD_NOT_RUN if outcome == Outcome.FAILED_ROLE_ERROR.value else UNEXPECTED
    return _ROW_CLASS.get(str(row.get("outcome") or ""), fallback)


def stop_view(kind: str, *, ran_s: Optional[float] = None, findings: int = 0) -> Dict[str, str]:
    """What the Floor says about a stop, each fact once: a short ``title``, a
    ``summary`` of what happened and what continuing does (``next``) -- from
    the class and typed numbers (run time, findings count), never from prose."""
    known = kind if kind in STOP_TEXT else UNEXPECTED
    title, happened, then = STOP_TEXT[known]
    minutes = int(round(float(ran_s or 0) / 60))
    ran = f" (it had run for {minutes} minute{'' if minutes == 1 else 's'})" if minutes else ""
    items = f" ({findings} finding{'' if findings == 1 else 's'})" if findings else ""
    summary = happened.format(ran=ran, items=items)
    return {"kind": known, "title": title, "summary": summary, "next": then}


def failed_label(triple: Mapping[str, str]) -> str:
    """``FAILED(<gate>, <check>, <finding>)`` -- the runner rule's own wording."""
    from app.factory.build.rule_decision import stop_status

    return stop_status(dict(triple))


def acceptance_score(status: Mapping[str, Any]) -> Dict[str, Any]:
    """``{passed, total}`` from the build status's acceptance record (k/N)."""
    acc = status.get("acceptance") or {}
    passed = acc.get("passed")
    total = acc.get("total")
    return {
        "passed": int(passed) if isinstance(passed, int) else None,
        "total": int(total) if isinstance(total, int) else None,
    }
