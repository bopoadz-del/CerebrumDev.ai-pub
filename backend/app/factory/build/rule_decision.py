"""The runner rule's decision record -- the ONE place its field names live.

Every failed phase verdict, at every gate, goes through one rule
(``runner.RoleRunner.decide``) and is recorded as a ``decision`` payload with
these typed fields. A STOP is the run's ONE terminal ledger event: a
``RUN_FAILED`` whose payload carries ``decision`` with ``class == STOP``. Other
code (build status, the Floor, export) reads these names from here.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

#: Payload key under which a decision record is written.
DECISION_KEY = "decision"

#: The record's typed fields.
GATE = "gate"  # the phase whose gate failed: WRITER / TESTER / STORE_MANAGER
CHECK = "check"  # the brief check id that failed
FINDING = "finding"  # the first finding, as the gate stated it
CLASS = "class"  # REWORK / ADVISORY / REGENERATE_TEST / REPROMPT / STOP
ROUND_GATE = "round_gate"  # this gate's rework round (n of its budget)
ROUND_BUILD = "round_build"  # the build's rework round (n of the ceiling)

#: The classes.
REWORK = "REWORK"
ADVISORY = "ADVISORY"
REGENERATE_TEST = "REGENERATE_TEST"
#: A writer pass that touched a Factory-owned file: the Factory's version is
#: put back and the writer re-prompted with the paths -- never a rework round
#: (owner, cycle 8: a writer that burns its budget on Factory files is a
#: FACTORY defect, never charged to the product).
REPROMPT = "REPROMPT"
STOP = "STOP"

#: A NOTE carrying this key resets the rework budget: rounds before it no
#: longer count toward any gate's budget or the build ceiling.
BUDGET_RESET_KEY = "rework_budget_reset"


def stop_record(event: Any) -> Optional[Dict[str, Any]]:
    """The STOP decision a terminal event carries, else None."""
    rec = (getattr(event, "payload", None) or {}).get(DECISION_KEY)
    if isinstance(rec, dict) and rec.get(CLASS) == STOP:
        return dict(rec)
    return None


def stop_status(rec: Dict[str, Any]) -> str:
    """How a stop reads: ``FAILED(<gate>, <check>, <finding>)``."""
    return f"FAILED({rec.get(GATE)}, {rec.get(CHECK)}, {rec.get(FINDING)})"


def reset_rework_budget(ledger: Any, *, reason: str) -> None:
    """THE entry point a resume calls to give every gate its full budget back.

    Appends one NOTE; the runner counts rework rounds (per gate and for the
    ceiling) only after the last reset. Same-failure-twice history resets
    with it: a resumed build is judged on what fails from here.
    """
    from app.factory.build.ledger import EventKind

    ledger.append(
        EventKind.NOTE,
        detail=f"rework budget reset: {reason}",
        payload={BUDGET_RESET_KEY: True, "reason": reason},
    )
