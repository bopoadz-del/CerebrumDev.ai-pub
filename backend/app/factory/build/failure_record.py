"""FAILED(<gate>, <check>, <finding>) -- the one reader of a failed build's verdict.

A failed build is still a product: it exports on the explicit ask, and its
zip MANIFEST, its Floor card and its "Continue" line all name the SAME three
facts. They are read here, once, from the run's terminal ledger event.

The runner rule (rule_decision.py) records a STOP as the terminal RUN_FAILED
payload's ``decision`` {gate, check, finding, class, ...}; it is read through
``rule_decision.stop_record``. A ledger written before that record existed
still carries the failing phase, the gate's reason and its findings -- the
same facts under older names, read as a fallback, never parsed out of prose.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Sequence

#: The terminal payload keys the runner rule writes.
GATE_KEY = "gate"
CHECK_KEY = "check"
FINDING_KEY = "finding"


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
    gate = str(payload.get(GATE_KEY) or trail.get("phase") or payload.get("phase") or "").strip()
    check = str(payload.get(CHECK_KEY) or trail.get("reason") or payload.get("reason") or "").strip()
    finding = str(
        payload.get(FINDING_KEY)
        or _first(payload.get("findings"))
        or trail.get("detail")
        or ""
    ).strip()
    if not (gate or check or finding):
        return None
    return {"gate": gate or "unknown", "check": check or "unknown", "finding": finding}


def failed_label(triple: Mapping[str, str]) -> str:
    """``FAILED(<gate>, <check>, <finding>)`` -- the runner rule's own wording."""
    from app.factory.build.rule_decision import stop_status

    return stop_status(dict(triple))


def next_continue_line(triple: Optional[Mapping[str, str]]) -> str:
    """One line: what the next "Continue" will try to fix."""
    if not triple:
        return ""
    finding = str(triple.get("finding") or "").strip()
    short = finding if len(finding) <= 160 else finding[:157] + "..."
    return (
        f"Continue will resume this platform's branch at {triple.get('gate')} "
        f"and send the writer the failing check {triple.get('check')}"
        + (f": {short}" if short else "")
    )


def acceptance_score(status: Mapping[str, Any]) -> Dict[str, Any]:
    """``{passed, total}`` from the build status's acceptance record (k/N)."""
    acc = status.get("acceptance") or {}
    passed = acc.get("passed")
    total = acc.get("total")
    return {
        "passed": int(passed) if isinstance(passed, int) else None,
        "total": int(total) if isinstance(total, int) else None,
    }
