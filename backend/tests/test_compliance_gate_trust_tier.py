"""Trust tier as a build gate: nobody vouched for this block.

The gate already refused capabilities whose blocks were unplanned, undeclared
or absent from one of the two registries. All three ask *does this block
exist where it should*. None asked *has anyone taken responsibility for it*.

An unvouched block read exactly like a vouched one, because the field did not
exist -- so "nobody has looked at this" and "this is fine" were the same
value. That is the gap these tests close.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import List, Tuple

from app.factory import compliance_gate as cg
from app.factory.dual_registry import load_factory_shelf


@dataclass
class _Cap:
    capability_id: str
    strategy: str = "REUSE"
    block_ids: Tuple[str, ...] = ()


@dataclass
class _Plan:
    capabilities: List[_Cap] = field(default_factory=list)
    dual_registered_blocks: Tuple[str, ...] = ()
    unsupported: Tuple[str, ...] = ()


def _plan(*block_ids: str) -> _Plan:
    return _Plan(
        capabilities=[_Cap("cap.reporting", "REUSE", tuple(block_ids))],
        dual_registered_blocks=tuple(block_ids),
    )


# -- the shelf as shipped --------------------------------------------------


def test_every_block_on_the_shelf_declares_an_accepted_tier():
    """The gate must refuse nothing that builds today.

    A gate that fails the current shelf gets switched off rather than fixed,
    so this pins that the tiers were actually stamped -- all 25 of them.
    """
    shelf = load_factory_shelf()
    assert shelf, "the factory shelf did not load"
    untiered = sorted(
        bid for bid, ref in shelf.items() if ref.trust_tier not in cg.ACCEPTED_TRUST_TIERS
    )
    assert untiered == [], f"blocks on the shelf with no accepted tier: {untiered}"


def test_the_shelf_json_carries_the_field_not_just_the_dataclass():
    """A default on BlockRef would satisfy the test above by itself.

    Reading the file directly proves the tier is data on disk rather than a
    dataclass default quietly filling in for absent provenance.
    """
    from app.factory.dual_registry import _factory_shelf_path

    raw = json.loads(_factory_shelf_path().read_text(encoding="utf-8"))
    tiers = [b.get("trust_tier") for b in raw["blocks"]]
    assert len(tiers) == 25
    assert all(t == "platform" for t in tiers), "shelf entries missing trust_tier in JSON"


# -- the gate --------------------------------------------------------------


def test_a_vouched_block_passes():
    verdict = cg.evaluate_plan(_plan("analytics"), trust_tiers={"analytics": "platform"})
    assert verdict.compliant
    assert verdict.gaps == ()


def test_a_block_with_no_tier_is_refused():
    """The case the gate exists for."""
    verdict = cg.evaluate_plan(_plan("contributed_thing"), trust_tiers={"contributed_thing": ""})

    assert not verdict.compliant
    reasons = [g.reason for g in verdict.gaps]
    assert cg.REASON_NO_TRUST_TIER in reasons
    assert "nobody has vouched" in verdict.gaps[0].detail


def test_a_block_absent_from_the_shelf_entirely_is_refused():
    verdict = cg.evaluate_plan(_plan("ghost"), trust_tiers={})
    assert cg.REASON_NO_TRUST_TIER in [g.reason for g in verdict.gaps]


def test_an_unrecognised_tier_does_not_pass_by_being_non_empty():
    """`trust_tier: "probably ok"` must not clear the gate.

    Accepting any non-empty string would make the field a comment.
    """
    verdict = cg.evaluate_plan(_plan("x"), trust_tiers={"x": "probably ok"})
    assert cg.REASON_NO_TRUST_TIER in [g.reason for g in verdict.gaps]


def test_contributor_reviewed_is_accepted():
    """A reviewer signing off is the other way a block earns its place."""
    verdict = cg.evaluate_plan(_plan("x"), trust_tiers={"x": "contributor_reviewed"})
    assert verdict.compliant


# -- not double-reporting, and not over-reaching ---------------------------


def test_an_unregistered_block_is_reported_once_as_a_registration_gap():
    """It is missing from the registry; that is the finding.

    Also reporting it as untrusted would name one defect twice and make the
    verdict read as two independent problems.
    """
    plan = _Plan(
        capabilities=[_Cap("cap.x", "REUSE", ("ghost",))],
        dual_registered_blocks=(),
    )
    verdict = cg.evaluate_plan(plan, trust_tiers={})

    reasons = [g.reason for g in verdict.gaps]
    assert cg.REASON_NOT_DUAL_REGISTERED in reasons
    assert cg.REASON_NO_TRUST_TIER not in reasons


def test_no_tiers_offered_makes_the_check_abstain():
    """A check that cannot run must not report a pass it did not perform --
    nor invent a failure it cannot substantiate.

    The same plan that is refused when tiers are supplied raises no trust gap
    when none are. Abstaining is the only honest third option, and it is what
    happens when ``load_trust_tiers()`` returns None because the shelf could
    not be read.
    """
    refused = cg.evaluate_plan(_plan("anything"), trust_tiers={"anything": ""})
    assert cg.REASON_NO_TRUST_TIER in [g.reason for g in refused.gaps]

    abstained = cg.evaluate_plan(_plan("anything"), trust_tiers=None)
    assert cg.REASON_NO_TRUST_TIER not in [g.reason for g in abstained.gaps]
    assert abstained.compliant, "abstaining must not manufacture a refusal"


def test_a_broken_shelf_file_does_not_break_the_gate(monkeypatch):
    """The loader swallows its own failure and returns None."""
    import app.factory.dual_registry as dr

    monkeypatch.setattr(
        dr, "load_factory_shelf", lambda *a, **k: (_ for _ in ()).throw(OSError("gone"))
    )
    assert cg.load_trust_tiers() is None


def test_a_capability_with_no_blocks_raises_no_trust_gap():
    """ADAPT/kernel generation names no blocks; there is nothing to vouch for."""
    plan = _Plan(capabilities=[_Cap("cap.kernel", "ADAPT", ())])
    verdict = cg.evaluate_plan(plan, trust_tiers={})
    assert cg.REASON_NO_TRUST_TIER not in [g.reason for g in verdict.gaps]
