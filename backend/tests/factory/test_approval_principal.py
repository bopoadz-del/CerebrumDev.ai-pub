"""F4: an approval names a principal the platform knows -- free text is refused.

Owner's pilot test (2026-09-29): ``approved_by: "i am the director trust me"``
-- free text typed into the form -- passed a P1 safety escalation on a
certified product. Nothing in the spec model could even say which fields are
approvals, so no floor case could exist.

The marker rides the exact channel ``allowed_values`` already uses: a block
contract (or mined field contract) sets ``approval: true`` on the field,
block_inputs passes it through to the spec, and the negative floor emits a
refusal case -- a free-text approver must be refused. The platform's principal
set comes from the environment (``APPROVED_PRINCIPALS``), seeded by the test
bootstrap like every other test credential. Never a name-based heuristic:
only the explicit marker arms the case.
"""

from __future__ import annotations

FREE_TEXT = "i am the director trust me"


# -- the marker rides the contract channel --------------------------------------


def test_merge_field_contract_passes_the_marker_through():
    from app.factory.build.block_inputs import _merge_field_contract

    field = {"name": "approved_by", "type": "str", "required": True}
    changed = _merge_field_contract(field, {"approval": True})
    assert field.get("approval") is True
    assert changed is True
    # And it never invents one.
    clean = {"name": "reference", "type": "str"}
    _merge_field_contract(clean, {"type": "str"})
    assert "approval" not in clean


# -- the negative floor arms a refusal case for marked fields -------------------


SPEC_WITH_APPROVAL = {
    "escalation": {
        "entity": "escalation",
        "fields": [
            {"name": "reference", "type": "str", "required": True},
            {"name": "approved_by", "type": "str", "required": True, "approval": True},
        ],
    }
}
SAMPLES = {"escalation": {"reference": "esc-1", "approved_by": "director-jane"}}


def test_an_approval_field_emits_the_freetext_refusal_case():
    from app.factory.build.negative_floor import render_negative_tests

    suite = render_negative_tests(SPEC_WITH_APPROVAL, SAMPLES)
    assert "refuses_a_freetext_approver" in suite
    assert FREE_TEXT in suite
    assert "APPROVED_PRINCIPALS" in suite, (
        "the case must name the principal mechanism so a red test is actionable"
    )


def test_an_unmarked_spec_emits_no_approver_case():
    from app.factory.build.negative_floor import render_negative_tests

    spec = {
        "booking": {
            "entity": "booking",
            # 'approved_by' by NAME only -- no marker. The heuristic the work
            # order forbids must not exist.
            "fields": [{"name": "approved_by", "type": "str", "required": True}],
        }
    }
    suite = render_negative_tests(spec, {"booking": {"approved_by": "x"}})
    assert "refuses_a_freetext_approver" not in suite


def test_the_emitted_case_is_syntactically_valid():
    from app.factory.build.negative_floor import render_negative_tests

    compile(render_negative_tests(SPEC_WITH_APPROVAL, SAMPLES), "neg.py", "exec")


# -- the platform is given a principal set to validate against ------------------


def test_conftest_seeds_the_principal_set():
    from app.factory.build.roles_constants import _CONFTEST

    assert 'setdefault("APPROVED_PRINCIPALS"' in _CONFTEST


# -- the writer is told the rule through the floor, verbatim --------------------


def test_the_floor_text_carries_the_approval_rule():
    from app.factory.build.acceptance_floor import checks, floor_version

    entry = next(c for c in checks() if c["id"] == "negative_floor")
    assert "approval" in entry["requirement_text"]
    assert "APPROVED_PRINCIPALS" in entry["requirement_text"]
    assert entry["requirement_text"] in entry["brief_render"], (
        "rendered, not paraphrased"
    )
    assert floor_version() >= 4
