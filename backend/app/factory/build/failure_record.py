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

from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

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


# ---------------------------------------------------------------------------
# One "ended because" record.
#
# A build that ended early is described by ONE record, read from the LAST
# failure the ledger holds. The first failure is history once a fix round has
# run, and the runner's last decision names a gate even when the run ended by
# time. The page writes its sentence from the record's typed fields; the raw
# message travels under ``technical`` and is never what the page reads.
# ---------------------------------------------------------------------------

#: The closed set of ways a build can end early.
STOP_TIME_RAN_OUT = "time_ran_out"
STOP_CHECK_FAILED = "check_failed"
STOP_NOTHING_PRODUCED = "nothing_produced"
STOP_SERVICE_INTERRUPTED = "service_interrupted"
STOP_NO_PROGRESS = "no_progress"
STOP_USER_STOPPED = "user_stopped"
STOP_NOT_FOUND = "not_found"
STOP_KINDS = (
    STOP_TIME_RAN_OUT,
    STOP_CHECK_FAILED,
    STOP_NOTHING_PRODUCED,
    STOP_SERVICE_INTERRUPTED,
    STOP_NO_PROGRESS,
    STOP_USER_STOPPED,
    STOP_NOT_FOUND,
)

#: Every code that can end a run -> (kind, one plain sentence). A code is a
#: reason token, a run outcome, a typed failure kind or a status honesty
#: marker. The sentences carry no ids, codes or paths: a person reads them.
#: tests/factory/test_stop_record.py fails when a code that can end a run has
#: no entry here.
STOP_ENTRIES: Dict[str, Tuple[str, str]] = {
    # Time ran out.
    "timeout": (STOP_TIME_RAN_OUT, "This step ran out of time before it finished."),
    "model_call_timeout": (STOP_TIME_RAN_OUT, "The coding service did not answer in time."),
    "worker_timed_out": (STOP_TIME_RAN_OUT, "This step ran out of time before it finished."),
    "time_budget_exceeded": (STOP_TIME_RAN_OUT, "The build reached its time limit before it finished."),
    "stage_time_check_stop": (
        STOP_TIME_RAN_OUT,
        "The build reached a time check without enough working code to finish in time.",
    ),
    "budget_skipped": (STOP_TIME_RAN_OUT, "A step was skipped because the build had no time left."),
    "FAILED_TIME_BUDGET": (STOP_TIME_RAN_OUT, "The build reached its time limit before it finished."),
    # No progress.
    "worker_stalled": (STOP_NO_PROGRESS, "This step made no progress for too long and was stopped."),
    # Nothing was produced.
    "writer_no_output": (STOP_NOTHING_PRODUCED, "This step finished without producing any code."),
    "FAILED_ROLE_ERROR": (STOP_NOTHING_PRODUCED, "A build step could not finish."),
    # Our service was interrupted.
    "build_thread_crashed": (STOP_SERVICE_INTERRUPTED, "Our build service stopped while this step was running."),
    "BUILD_THREAD_CRASHED": (STOP_SERVICE_INTERRUPTED, "Our build service stopped while this step was running."),
    "BUILD_THREAD_ORPHANED": (STOP_SERVICE_INTERRUPTED, "Our build service restarted while this build was running."),
    "FACTORY_CODE_CLI_ORPHANED": (
        STOP_SERVICE_INTERRUPTED,
        "Our build service restarted while the code was being written.",
    ),
    "LEDGER_UNREADABLE": (STOP_SERVICE_INTERRUPTED, "The build's own record could not be read."),
    "LEDGER_EXTERNAL_NOTE_QUARANTINED": (
        STOP_SERVICE_INTERRUPTED,
        "The build's own record was damaged, so the build stopped.",
    ),
    "codewhale_worker_failed": (STOP_SERVICE_INTERRUPTED, "The coding service failed while writing the code."),
    "worker_concurrency_capped": (STOP_SERVICE_INTERRUPTED, "The coding service was full and could not take this step."),
    "worker_dispatch_ambiguous": (STOP_SERVICE_INTERRUPTED, "The coding service could not confirm this step had started."),
    "worker_cli_missing": (STOP_SERVICE_INTERRUPTED, "The coding service is not installed on our build server."),
    "worker_exec_failed": (STOP_SERVICE_INTERRUPTED, "The coding service exited before it finished."),
    "worker_cap_malformed": (STOP_SERVICE_INTERRUPTED, "The coding service's limits are set up wrongly on our side."),
    "worker_profile_unsupported": (STOP_SERVICE_INTERRUPTED, "The coding service's settings are not supported on our side."),
    "worker_profile_unknown": (STOP_SERVICE_INTERRUPTED, "The coding service's settings are unknown on our side."),
    "worker_provider_refused": (STOP_SERVICE_INTERRUPTED, "The coding service's provider refused the request."),
    "slot_wait_exhausted": (STOP_SERVICE_INTERRUPTED, "No build slot came free in time."),
    "docker_unavailable": (STOP_SERVICE_INTERRUPTED, "Our packaging service could not be reached."),
    "packager_failed": (STOP_SERVICE_INTERRUPTED, "Packaging the platform for delivery failed."),
    "github_delivery_failed": (STOP_SERVICE_INTERRUPTED, "Sending the platform to its repository failed."),
    # A check did not pass.
    "FAILED_GATE": (STOP_CHECK_FAILED, "A quality check did not pass."),
    "FAILED_BUDGET_SPENT": (STOP_CHECK_FAILED, "A quality check still did not pass after the fix rounds."),
    "FAILED_AUTHORITY": (STOP_CHECK_FAILED, "Code was written outside the area its step may change."),
    "COLLECT_ALL_REPORT": (STOP_CHECK_FAILED, "A test run listed checks that did not pass."),
    "writer_behaviour_failed": (STOP_CHECK_FAILED, "A feature did not accept its own request."),
    "factory_substrate_conflict": (STOP_CHECK_FAILED, "The written code changed files the Factory owns."),
    "vendor_transform_broke_source": (STOP_CHECK_FAILED, "Preparing a building block broke its source code."),
    # The owner stopped it.
    "stopped_by_owner": (STOP_USER_STOPPED, "You stopped this build."),
    "STOPPED_BY_OWNER": (STOP_USER_STOPPED, "You stopped this build."),
}

#: Run outcomes that decide the kind on their own, whatever failed before:
#: the owner's stop and the build's own time wall end the run themselves.
DECISIVE_OUTCOMES = frozenset({"STOPPED_BY_OWNER", "FAILED_TIME_BUDGET"})

#: The sentence for a check that has no entry of its own.
CHECK_FAILED_PLAIN = "A quality check did not pass."
#: The sentence for a build that went quiet with no marker of why.
NO_PROGRESS_PLAIN = "The build stopped sending updates, so nothing is working on it."
#: The sentence for a build the server cannot find.
NOT_FOUND_PLAIN = "We can't find this build on the server."

#: The runner decision classes that spend a fix round.
FIX_ROUND_CLASSES = frozenset({"REWORK", "REGENERATE_TEST", "REPROMPT"})

_CODE_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")


def _is_code(token: str) -> bool:
    return bool(token) and token[0].isalpha() and set(token) <= _CODE_CHARS


def reason_chain(reason: Any, detail: Any = "") -> List[str]:
    """The reason codes of one failure, outermost first.

    A role error records its outer code as ``reason`` and the inner ones as
    the leading ``code: code: prose`` of its message. The chain is every
    leading code of each, stopping at the first piece of prose."""
    chain: List[str] = []
    for source in (reason, detail):
        for part in str(source or "").split(":"):
            token = part.strip()
            if not _is_code(token):
                break
            if token not in chain:
                chain.append(token)
    return chain


def stop_kind(
    *,
    outcome: Any = None,
    markers: Sequence[str] = (),
    failure_kind: Any = None,
    chain: Sequence[str] = (),
    gate_failed: bool = False,
) -> Tuple[str, str, str]:
    """``(kind, plain, code)`` for a build that ended early.

    Read in this order: an outcome that decides on its own (the owner's stop,
    the time wall); a marker of how the run itself ended (the terminal
    event's own codes, or the status's honesty marker for a run that never
    wrote one), innermost first; the last failure's typed kind (a timeout by
    exception type); its reason chain, innermost code first, because the
    innermost is the cause; a gate refusal with no entry of its own, which is
    a check that did not pass; the run outcome."""
    outcome = str(outcome or "")
    if outcome in DECISIVE_OUTCOMES:
        kind, plain = STOP_ENTRIES[outcome]
        return kind, plain, outcome
    for code in reversed(list(markers)):
        if code in STOP_ENTRIES:
            kind, plain = STOP_ENTRIES[code]
            return kind, plain, code
    typed = str(failure_kind or "")
    if typed in STOP_ENTRIES:
        kind, plain = STOP_ENTRIES[typed]
        return kind, plain, typed
    for code in reversed(list(chain)):
        if code in STOP_ENTRIES:
            kind, plain = STOP_ENTRIES[code]
            return kind, plain, code
    first = chain[0] if chain else ""
    if gate_failed:
        return STOP_CHECK_FAILED, CHECK_FAILED_PLAIN, first
    if outcome in STOP_ENTRIES:
        kind, plain = STOP_ENTRIES[outcome]
        return kind, plain, outcome
    return STOP_CHECK_FAILED, CHECK_FAILED_PLAIN, first or outcome


def fix_rounds_used(decisions: Optional[Sequence[Any]]) -> int:
    """How many fix rounds the runner spent: its decisions that sent work back."""
    return sum(
        1
        for d in decisions or ()
        if isinstance(d, Mapping) and str(d.get("class") or "") in FIX_ROUND_CLASSES
    )
