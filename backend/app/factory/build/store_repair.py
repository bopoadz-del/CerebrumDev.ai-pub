"""R4 + R5 (Advisor 12:13Z, spec section K, item K-2): a Store block that
misbehaves in a build is repaired under its OWN allowance, and the defect is
the Store's, never the writer's.

* R4. Each defective block gets ``STORE_REPAIR_ALLOWANCE`` repair rounds of
  its own. They are counted from ``NOTE`` events carrying ``store_repair``,
  never from REWORK, so the writer's grader rework count (``REWORK_BUDGET``)
  cannot see them. A repair that fails, or a block whose allowance is spent,
  marks only the capabilities bound to that block as not delivered, with
  :data:`REPAIR_FAILED_REASON`, and the rest of the build goes on.

* R5. The grade record gains a ``store_defects`` section (block, kinds,
  repaired yes/no, exam id and score, capabilities not delivered). These
  rows are never writer items.

:func:`settle` only acts on a NOT_CERTIFIED verdict whose every open item is
a finding inside a vendored block (``vendor/blocks/<id>/``, read from the
path, never from a list of names). Anything the writer owns, any ungraded
layer and any other Factory finding leaves the verdict to the paths that
handle it today. The repair itself (R2: an exam from the block's contract)
is the ``repairer`` the caller passes; K-3 supplies it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from app.factory.build.store_override import (
    REPAIR_FAILED_REASON,
    StoreDefect,
    block_of_path,
    store_defects,
)

#: R4: repair rounds per defective block (one round, the spec's default).
STORE_REPAIR_ALLOWANCE = 1
#: NOTE payload key naming the block a repair round was spent on.
STORE_REPAIR_KEY = "store_repair"
#: NOTE payload key naming the block a Store defect was recorded for.
STORE_DEFECT_KEY = "store_defect"
#: grade.json section (R5).
GRADE_SECTION = "store_defects"
#: Why a block got no repair round: its allowance was already spent.
ALLOWANCE_SPENT = "repair allowance spent"


@dataclass(frozen=True)
class RepairResult:
    """What a repairer reports for one block: did the repair pass its exam."""

    block_id: str
    passed: bool
    exam_id: str = ""
    exam_score: Optional[float] = None
    detail: str = ""


#: ``repairer(block_id, defects) -> RepairResult``.
Repairer = Callable[[str, Tuple[StoreDefect, ...]], RepairResult]


@dataclass(frozen=True)
class BlockRepair:
    """R4/R5: the outcome for one defective block in this grader verdict."""

    block_id: str
    kinds: Tuple[str, ...]
    attempted: bool
    repaired: bool
    exam_id: str = ""
    exam_score: Optional[float] = None
    not_delivered: Tuple[str, ...] = ()
    reason: str = ""
    detail: str = ""

    def row(self) -> Dict[str, Any]:
        """The grade.json row (R5)."""
        return {
            "block_id": self.block_id,
            "kinds": list(self.kinds),
            "repaired": self.repaired,
            "exam_id": self.exam_id,
            "exam_score": self.exam_score,
            "not_delivered": list(self.not_delivered),
            "reason": self.reason,
        }

    def note(self, repair_round: int) -> Dict[str, Any]:
        """The ledger NOTE payload. Only an attempted repair carries
        :data:`STORE_REPAIR_KEY`, so only it spends the block's allowance."""
        payload: Dict[str, Any] = {STORE_DEFECT_KEY: self.block_id, **self.row()}
        payload["detail"] = self.detail
        if self.attempted:
            payload[STORE_REPAIR_KEY] = self.block_id
            payload["repair_round"] = repair_round
        return payload


def repairs_spent(events: Iterable[Any], block_id: str) -> int:
    """R4: repair rounds already spent on ``block_id``, from NOTE events only."""
    count = 0
    for event in events:
        if getattr(getattr(event, "kind", None), "value", None) != "NOTE":
            continue
        if (getattr(event, "payload", None) or {}).get(STORE_REPAIR_KEY) == block_id:
            count += 1
    return count


def capabilities_of(plan_capabilities: Iterable[Any], block_id: str) -> Tuple[str, ...]:
    """The planned capabilities bound to ``block_id`` (their ``block_ids``)."""
    out: List[str] = []
    for cap in plan_capabilities or []:
        if block_id in (getattr(cap, "block_ids", None) or []):
            out.append(str(getattr(cap, "capability_id", "") or ""))
    return tuple(c for c in out if c)


def factory_findings(grade: Optional[Mapping[str, Any]]) -> List[Mapping[str, Any]]:
    """The grade's hardwiring findings the writer does not own."""
    from app.factory.build.product_grader.grade_record import finding_is_writers

    findings = ((grade or {}).get("hardwiring") or {}).get("findings") or []
    return [f for f in findings if isinstance(f, Mapping) and not finding_is_writers(f)]


def _store_only(verdict: Any) -> List[StoreDefect]:
    """The verdict's Store defects when they are ALL that is open, else []."""
    if verdict.writer_items or verdict.ungraded:
        return []
    owed = list(verdict.factory_owed)
    if not owed or list(verdict.items) != owed:
        return []
    findings = factory_findings(verdict.grade)
    if len(findings) != len(owed):
        return []
    for finding in findings:
        if block_of_path(finding.get("file") or finding.get("path")) is None:
            return []
    return store_defects(findings)


def _repair_one(
    repairer: Repairer, block_id: str, defects: Tuple[StoreDefect, ...]
) -> RepairResult:
    try:
        result = repairer(block_id, defects)
    except Exception as exc:  # noqa: BLE001 -- a crashed repair is a failed one, named
        return RepairResult(block_id, False, detail=f"repair crashed: {type(exc).__name__}: {exc}")
    if not isinstance(result, RepairResult):
        return RepairResult(block_id, False, detail="repairer returned no RepairResult")
    return result


def settle(
    verdict: Any,
    *,
    plan_capabilities: Sequence[Any],
    events: Iterable[Any],
    repairer: Repairer,
    allowance: int = STORE_REPAIR_ALLOWANCE,
) -> Optional[Tuple[BlockRepair, ...]]:
    """R4: repair each defective block within its own allowance.

    Returns None when the verdict is not made only of Store defects (the
    caller's existing path decides it). Otherwise one :class:`BlockRepair`
    per block: repaired, or not, with the bound capabilities not delivered."""
    defects = _store_only(verdict)
    if not defects:
        return None
    events = list(events)
    by_block: Dict[str, List[StoreDefect]] = {}
    for defect in defects:
        by_block.setdefault(defect.block_id, []).append(defect)
    out: List[BlockRepair] = []
    for block_id in sorted(by_block):
        block_defects = tuple(by_block[block_id])
        kinds = tuple(sorted({d.kind for d in block_defects}))
        if repairs_spent(events, block_id) >= allowance:
            result = RepairResult(block_id, False, detail=ALLOWANCE_SPENT)
            attempted = False
        else:
            result = _repair_one(repairer, block_id, block_defects)
            attempted = True
        repaired = result.passed is True
        out.append(
            BlockRepair(
                block_id=block_id,
                kinds=kinds,
                attempted=attempted,
                repaired=repaired,
                exam_id=str(result.exam_id or ""),
                exam_score=result.exam_score,
                not_delivered=() if repaired else capabilities_of(plan_capabilities, block_id),
                reason="" if repaired else REPAIR_FAILED_REASON,
                detail=str(result.detail or ""),
            )
        )
    return tuple(out)


def grade_with_section(
    grade: Optional[Mapping[str, Any]], repairs: Sequence[BlockRepair]
) -> Dict[str, Any]:
    """R5: the grade record with its ``store_defects`` section."""
    out = dict(grade or {})
    out[GRADE_SECTION] = [r.row() for r in repairs]
    return out
