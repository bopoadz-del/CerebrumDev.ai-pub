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

F-3 (grader 21:50Z): a passed exam is not yet a repair. A block counts as
repaired only when the caller's ``installer`` has put the examined repair in
place of the vendored copy, recorded its override and passed the lock check
(it returns None; any other value is the reason it did not). The caller then
re-grades the product, and :func:`regrade_settles` decides from that new
verdict: what is still open may only be Store defects of blocks whose repair
failed, and those blocks' capabilities are marked not delivered.
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
#: ``installer(result, defects) -> None`` when the passed repair is installed,
#: recorded and passes the lock check; otherwise the reason it is not (F-3).
Installer = Callable[[RepairResult, Tuple[StoreDefect, ...]], Optional[str]]


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


def _install_one(
    installer: Installer, result: RepairResult, defects: Tuple[StoreDefect, ...]
) -> Optional[str]:
    """F-3: None when ``installer`` installed the passed repair, else why not."""
    try:
        refusal = installer(result, defects)
    except Exception as exc:  # noqa: BLE001 -- a crashed install is a failed one, named
        return f"install crashed: {type(exc).__name__}: {exc}"
    if refusal is None:
        return None
    return f"not installed: {refusal}"


def settle(
    verdict: Any,
    *,
    plan_capabilities: Sequence[Any],
    events: Iterable[Any],
    repairer: Repairer,
    installer: Installer,
    allowance: int = STORE_REPAIR_ALLOWANCE,
) -> Optional[Tuple[BlockRepair, ...]]:
    """R4: repair each defective block within its own allowance.

    Returns None when the verdict is not made only of Store defects (the
    caller's existing path decides it). Otherwise one :class:`BlockRepair`
    per block: repaired (its exam passed AND ``installer`` installed it,
    F-3), or not, with the bound capabilities not delivered."""
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
        detail = str(result.detail or "")
        if repaired:
            refusal = _install_one(installer, result, block_defects)
            if refusal is not None:
                repaired = False
                detail = "; ".join(d for d in (detail, refusal) if d)
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
                detail=detail,
            )
        )
    return tuple(out)


def outcome_word(repair: BlockRepair, settled: Optional[bool]) -> str:
    """F-3b: what the block's ledger note says. A repair installed but not
    yet confirmed by a settling re-grade is "installed", never "repaired"."""
    if not repair.repaired:
        return repair.reason
    if settled is True:
        return "repaired"
    return "installed, but the re-grade did not settle the run"


def grade_with_section(
    grade: Optional[Mapping[str, Any]], repairs: Sequence[BlockRepair]
) -> Dict[str, Any]:
    """R5: the grade record with its ``store_defects`` section."""
    out = dict(grade or {})
    out[GRADE_SECTION] = [r.row() for r in repairs]
    return out


def regrade_settles(verdict: Any, repairs: Sequence[BlockRepair]) -> bool:
    """F-3: does the re-grade taken after the repairs settle the run?

    Yes when it is certified, or when everything it still has open is a
    Store defect (as :func:`settle` reads one) of a block whose repair
    failed, so its capabilities are already marked not delivered. A defect
    left in a block reported repaired, or anything else open, is no."""
    if getattr(verdict, "ok", False) is True:
        return True
    failed = {r.block_id for r in repairs if not r.repaired}
    left = _store_only(verdict)
    if not left:
        return False
    return all(d.block_id in failed for d in left)


def not_delivered(events: Iterable[Any]) -> List[Dict[str, Any]]:
    """F-3c (a): what the ledger already says is not delivered, for the
    build's API record. Read-only: each block's newest Store-defect NOTE
    decides, and only blocks whose note names capabilities are listed."""
    newest: Dict[str, Mapping[str, Any]] = {}
    for event in events:
        payload = getattr(event, "payload", None) or {}
        block_id = payload.get(STORE_DEFECT_KEY)
        if block_id:
            newest[str(block_id)] = payload
    return [
        {
            "block_id": block_id,
            "capabilities": list(payload.get("not_delivered") or ()),
            "reason": str(payload.get("reason") or ""),
        }
        for block_id, payload in newest.items()
        if payload.get("not_delivered")
    ]
