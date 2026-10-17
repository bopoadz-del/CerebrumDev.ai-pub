"""Generation-time contract compliance: refuse to build an incoherent plan.

The planner already refuses UNSUPPORTED capabilities. This gate refuses the
quieter failures a plan can carry while still looking well-formed:

- a capability planned as REUSE or COMPOSE that names no block. The
  strategy says "an existing block does this"; an empty block list says
  nothing does. The product ships a capability backed by nothing. (ADAPT is
  excluded on purpose -- see BLOCK_BACKED_STRATEGIES.)
- a block referenced by a capability but absent from the plan's own
  dual-registration list.
- a capability the blueprint asked for that no plan entry answers.

Wired inside ``generate_product``, which documents itself as the single
production door, rather than at its call sites -- there are seven, and a
gate maintained by remembering to call it at seven places is the defect
this module exists to prevent.

NOT checked here, and deliberately named rather than silently absent:
**trust tier**. No block declares one. ``tier`` already exists in
``universal_kernel``'s manifest meaning the PRICING tier ("premium"), so
reusing that key would collide two unrelated meanings on the asset a trust
decision depends on. The key has to be named before this can enforce it,
and a gate that claims to check tiers while reading a price would be worse
than one that admits it does not.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

#: Strategies whose name asserts that an existing block does the work.
#:
#: ADAPT is deliberately absent. The planner treats a blockless ADAPT as
#: kernel/template generation ("kernel/template generation (no block ids)"),
#: so flagging it here would make this gate contradict the planner and
#: refuse a build the planner considers well-formed. A gate that disagrees
#: with the component it guards gets switched off, not fixed.
BLOCK_BACKED_STRATEGIES = frozenset({"REUSE", "COMPOSE"})

REASON_UNSUPPORTED = "unsupported_capability"
REASON_NO_BLOCK_DECLARES = "no_block_declares"
REASON_NOT_DUAL_REGISTERED = "not_dual_registered"
REASON_NOT_PLANNED = "capability_not_planned"
REASON_NO_TRUST_TIER = "no_trust_tier"

#: Tiers the platform will build on, and what each one asserts.
#:
#: These are PROVENANCE claims, not quality scores. ``platform`` says the
#: block ships inside this repository, so the platform authored it and can
#: vouch for it by construction. ``contributor_reviewed`` says a named
#: reviewer read a contributed block and signed it off.
#:
#: What is deliberately NOT here is the empty string. A block carrying no
#: tier has had nobody vouch for it, and "nobody has looked at this" must not
#: read the same as "this is fine" -- which is exactly what it read as while
#: the field did not exist. Every block on the shelf today is ``platform``,
#: so this gate refuses nothing that currently builds; it fires the first
#: time an unvouched block reaches a plan.
ACCEPTED_TRUST_TIERS = frozenset({"platform", "contributor_reviewed"})


@dataclass(frozen=True)
class CapabilityGap:
    """One reason a plan may not be built."""

    capability_id: str
    reason: str
    detail: str = ""

    def to_dict(self) -> Dict[str, str]:
        return {
            "capability_id": self.capability_id,
            "reason": self.reason,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class ComplianceVerdict:
    compliant: bool
    gaps: Tuple[CapabilityGap, ...] = ()
    checked: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "compliant": self.compliant,
            "gaps": [g.to_dict() for g in self.gaps],
            "checked": list(self.checked),
        }

    def summary(self) -> str:
        if self.compliant:
            return "compliant: %d capability(ies) checked" % len(self.checked)
        return "; ".join(
            "%s (%s)%s" % (g.capability_id, g.reason, ": " + g.detail if g.detail else "")
            for g in self.gaps
        )


class ComplianceError(RuntimeError):
    """Raised instead of generating from a plan with gaps."""

    def __init__(self, verdict: ComplianceVerdict):
        super().__init__("refusing to generate: " + verdict.summary())
        self.verdict = verdict


def load_trust_tiers() -> Optional[Dict[str, str]]:
    """block_id -> trust tier, from the Factory shelf. None when unreadable.

    Deliberately NOT called by :func:`evaluate_plan` itself. Every other check
    in this module reads from the plan, which keeps the gate pure, fast and
    testable without a filesystem. Having it reach out to disk on its own
    would also make it disagree with the planner, whose
    ``dual_registered_blocks`` is a claim the gate otherwise takes at face
    value -- and a gate that contradicts the component it guards gets
    switched off, not fixed.

    So the production call site supplies the tiers (see
    ``product_architect``), and a caller that passes nothing gets no trust
    verdict rather than a guessed one.
    """
    try:
        from .dual_registry import load_factory_shelf

        return {bid: ref.trust_tier for bid, ref in load_factory_shelf().items()}
    except Exception:  # noqa: BLE001 -- a missing shelf must not break the gate
        return None


def evaluate_plan(
    plan: Any,
    *,
    blueprint: Optional[Any] = None,
    trust_tiers: Optional[Dict[str, str]] = None,
) -> ComplianceVerdict:
    """Return a verdict on ``plan``. Never raises on a bad plan; reports it.

    ``blueprint`` is optional because the coverage check is only meaningful
    when the request is available. Its absence narrows what can be checked
    and is not itself a gap -- a check that cannot run must not report a
    pass it did not perform, nor a failure it cannot substantiate.
    """
    gaps: List[CapabilityGap] = []
    #: None means no caller offered tiers, so the trust check abstains. It
    #: must not fall back to loading the shelf: see load_trust_tiers().
    shelf = trust_tiers
    capabilities = list(getattr(plan, "capabilities", ()) or ())
    checked = [c.capability_id for c in capabilities]
    dual = set(getattr(plan, "dual_registered_blocks", ()) or ())

    for cap_id in getattr(plan, "unsupported", ()) or ():
        gaps.append(
            CapabilityGap(
                cap_id,
                REASON_UNSUPPORTED,
                "the planner resolved no strategy for this capability",
            )
        )

    for cap in capabilities:
        strategy = getattr(cap, "strategy", "")
        block_ids = list(getattr(cap, "block_ids", ()) or ())

        if strategy in BLOCK_BACKED_STRATEGIES and not block_ids:
            gaps.append(
                CapabilityGap(
                    cap.capability_id,
                    REASON_NO_BLOCK_DECLARES,
                    "strategy %s claims an existing block provides this, but "
                    "the plan names none" % strategy,
                )
            )

        missing = [b for b in block_ids if b not in dual]
        if missing:
            gaps.append(
                CapabilityGap(
                    cap.capability_id,
                    REASON_NOT_DUAL_REGISTERED,
                    "block(s) absent from the plan's dual-registered set: "
                    + ", ".join(sorted(missing)),
                )
            )

        # Trust. Runs only on blocks that cleared dual registration, so a
        # block absent from the shelf is reported once, as the registration
        # gap it is, rather than twice under two names.
        if shelf is not None:
            unvouched = sorted(
                b
                for b in block_ids
                if b not in missing
                and (shelf.get(b) or "") not in ACCEPTED_TRUST_TIERS
            )
            if unvouched:
                gaps.append(
                    CapabilityGap(
                        cap.capability_id,
                        REASON_NO_TRUST_TIER,
                        "block(s) carry no accepted trust tier, so nobody has "
                        "vouched for them: " + ", ".join(unvouched),
                    )
                )

    if blueprint is not None:
        planned = {c.capability_id for c in capabilities}
        planned |= set(getattr(plan, "unsupported", ()) or ())
        for cap in getattr(blueprint, "capabilities", ()) or ():
            cap_id = getattr(cap, "id", None)
            if cap_id and cap_id not in planned:
                gaps.append(
                    CapabilityGap(
                        cap_id,
                        REASON_NOT_PLANNED,
                        "requested in the blueprint but absent from the plan",
                    )
                )
                checked.append(cap_id)

    return ComplianceVerdict(
        compliant=not gaps, gaps=tuple(gaps), checked=tuple(checked)
    )


def assert_compliant(
    plan: Any,
    *,
    blueprint: Optional[Any] = None,
    trust_tiers: Optional[Dict[str, str]] = None,
) -> Any:
    """Return ``plan``, or raise ComplianceError describing every gap.

    Every gap is reported, not just the first: a caller fixing one gap at a
    time learns the shape of the problem one build at a time.

    ``trust_tiers`` is forwarded to :func:`evaluate_plan`; omitting it means
    the trust check abstains rather than passing by default.
    """
    verdict = evaluate_plan(plan, blueprint=blueprint, trust_tiers=trust_tiers)
    if not verdict.compliant:
        raise ComplianceError(verdict)
    return plan
