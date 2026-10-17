"""A gate exists only if a brief can turn it on.

The owner's standing order (AGENTS.md, GATES.md), replacing "after every
build, add the failure a gate missed to GATES.md and patch the Store":

    A gate failure the brief never defined means the GATE is wrong, not the
    product: the gate goes advisory immediately, with the reason in the
    ledger, and no patch is written.

The old order grew the bar after every build -- each miss became a new gate,
and each new gate became writer rounds the customer paid for on a check their
brief never asked for. This module is how the runner tells the two apart
BEFORE a writer is dispatched.

* A failure names the check it measures: a verdict's ``payload["check"]``
  (one id for every finding), ``payload["finding_checks"]`` (one id per
  finding, parallel to ``findings``), and otherwise the gate's own name.
* THIS build's brief defines a check when its compiled brief turns it on:
  the ``[check:<id>]`` ids of the compiled ACCEPTANCE slot, plus the
  acceptance-floor checks the brief enforces (universal ones, and the
  conditionals whose ``applies_when`` signal the brief raised).
* Anything else is factory-invented. It is recorded as advisory, with the
  reason, and never reaches the writer's work list.

Decided by ids the compiler and the floor file declare, never by reading the
text of a finding.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, FrozenSet, Iterable, List, Optional, Tuple

#: The compiled brief's "own gates green" bullet: the code-phase suite.
SUITE_CHECK = "gates"
#: The compiled brief's PRODUCT-gate bullet: the pilot-marked suite.
PRODUCT_GATE_CHECK = "product_gate"

#: The WRITER gate's sub-checks -- the gate name each WRITER verdict carries.
#: The compiled brief renders one ACCEPTANCE line per id (what to build), so
#: a WRITER failure on any of them is brief-defined and goes back to the
#: writer as a rework round; the gates stamp these same constants.
WORKSPACE_COMPILES_CHECK = "workspace_compiles"
WRITER_CONTRACT_CHECK = "writer_contract"
WRITER_BEHAVIOUR_CHECK = "writer_behaviour"
UI_SURFACE_CHECK = "ui_surface"
UI_END_TO_END_CHECK = "ui_end_to_end"
WRITER_CHECKS = (
    WORKSPACE_COMPILES_CHECK,
    WRITER_CONTRACT_CHECK,
    WRITER_BEHAVIOUR_CHECK,
    UI_SURFACE_CHECK,
    UI_END_TO_END_CHECK,
)

#: Why a factory-invented failure is advisory -- the reason in the ledger.
REASON_NOT_DEFINED = "not defined by the brief"


def _norm(check: Any) -> str:
    return str(check or "").strip().lower()


def declare_check(verdict: Any, check: str) -> Any:
    """``verdict`` stamped with the check id it measures, unless the verdict
    already names one."""
    payload = dict(getattr(verdict, "payload", None) or {})
    if payload.get("check"):
        return verdict
    payload["check"] = check
    return replace(verdict, payload=payload)


def failure_checks(verdict: Any) -> List[Tuple[str, str]]:
    """``(check id, finding)`` for every failure a verdict carries.

    A verdict with no findings is one failure: the gate itself, with its
    detail.
    """
    payload = getattr(verdict, "payload", None) or {}
    own = _norm(payload.get("check") or getattr(verdict, "gate", ""))
    per = list(payload.get("finding_checks") or [])
    # Findings stay themselves (typed Finding objects keep their fields);
    # a typed finding names its own check, decided by data, never its text.
    findings = [
        f if isinstance(f, str) else str(f)
        for f in (getattr(verdict, "findings", None) or [])
    ]
    if not findings:
        return [(own, str(getattr(verdict, "detail", "") or own))]
    out = []
    for index, finding in enumerate(findings):
        named = _norm(per[index]) if index < len(per) else ""
        typed_check = _norm(getattr(finding, "check_id", ""))
        out.append((named or typed_check or own, finding))
    return out


@dataclass(frozen=True)
class FailureSplit:
    """A round's failures, split by whether this build's brief defined them."""

    defined: Tuple[Tuple[str, str], ...]
    invented: Tuple[Tuple[str, str], ...]

    @property
    def defined_findings(self) -> List[str]:
        return [finding for _, finding in self.defined]

    @property
    def defined_checks(self) -> List[str]:
        return sorted({check for check, _ in self.defined})

    @property
    def invented_checks(self) -> List[str]:
        return sorted({check for check, _ in self.invented})


def split_failures(verdict: Any, defined: FrozenSet[str]) -> FailureSplit:
    """Split ``verdict``'s failures into brief-defined and factory-invented."""
    keep, drop = [], []
    for check, finding in failure_checks(verdict):
        (keep if check in defined else drop).append((check, finding))
    return FailureSplit(defined=tuple(keep), invented=tuple(drop))


def narrowed(verdict: Any, split: FailureSplit) -> Any:
    """``verdict`` carrying only its brief-defined failures -- what the writer
    may be handed."""
    payload = dict(getattr(verdict, "payload", None) or {})
    payload["finding_checks"] = [check for check, _ in split.defined]
    if split.invented:
        payload["advisory_checks"] = split.invented_checks
    return replace(verdict, findings=split.defined_findings, payload=payload)


def advisory_checks(events: Iterable[Any]) -> List[dict]:
    """Every check this build moved to advisory, read back from its ledger:
    ``[{check, reason, findings_count, findings}]``, one row per check,
    findings summed across rounds and the distinct evidence rows themselves
    listed. The build status and the export manifest both carry this, so
    nothing is silenced out of sight."""
    rows: dict = {}
    for event in events:
        for entry in (getattr(event, "payload", None) or {}).get("gate_advisory") or []:
            check = _norm(entry.get("check"))
            if not check:
                continue
            row = rows.setdefault(
                check, {"check": check, "reason": "", "findings_count": 0, "findings": []}
            )
            row["reason"] = str(entry.get("reason") or REASON_NOT_DEFINED)
            found = [str(f) for f in entry.get("findings") or []]
            row["findings_count"] += len(found)
            row["findings"].extend(f for f in dict.fromkeys(found) if f not in row["findings"])
    return [rows[check] for check in sorted(rows)]


def brief_defined_checks(
    blueprint: Any, acceptance_checks: Iterable[str]
) -> FrozenSet[str]:
    """Every check THIS build's brief turns on: its compiled ACCEPTANCE ids
    plus the acceptance-floor checks it enforces."""
    from app.factory.build.acceptance_floor import enforced_ids

    return frozenset(_norm(c) for c in enforced_ids(blueprint)) | frozenset(
        _norm(c) for c in acceptance_checks if _norm(c)
    )


def compiled_acceptance_checks(
    blueprint: Any, plan: Any, *, blocks_root: Optional[Any] = None
) -> Tuple[str, ...]:
    """The ACCEPTANCE ids of this blueprint's compiled brief, compiled offline
    (no Store HTTP) by the same compiler the WRITER uses."""
    from app.factory.build.brief_compiler import compile_brief

    return compile_brief(
        blueprint, plan, blocks_root=blocks_root, store_ids=()
    ).acceptance_checks
