"""A Store-gate FAIL reaches the writer as ITS OWN check, never another one.

Live 82a19a82 (co-op repro build): the gate scored "acceptance.py in Docker
21/22 FAIL:audit_clean", yet the writer's rework item read
"[no_token_401] ... FAIL:audit_clean. Build: Every /v1 route refuses an
unauthenticated request with HTTP 401" and the run stopped
SAME_FAILURE_TWICE. The Factory asked for the artifact ``store-gate`` while the
workflow uploads ``store-gate-<sha>`` (an exact-match filter), so a red run
was never itemised, every floor line wore the red status, and the first
product-owned line became the work item.
"""

from __future__ import annotations

from app.factory.build import n3_store_gate as gate
from app.factory.build.acceptance_floor import PRODUCT
from app.factory.build.store_acceptance import ACCEPTANCE_CHECK_NAMES, NOT_RUN

SHA = "f" * 40


def _payload(failing):
    return {
        "lines": [
            {
                "name": name,
                "status": "FAIL" if name in failing else "PASS",
                "detail": f"{name} detail" if name in failing else "ok",
            }
            for name in ACCEPTANCE_CHECK_NAMES
        ],
        "passed": len(ACCEPTANCE_CHECK_NAMES) - len(failing),
        "total": len(ACCEPTANCE_CHECK_NAMES),
        "ok": False,
    }


def _verdict(report):
    failed = [line.name for line in report.lines if line.owner == PRODUCT and line.failed]
    return gate.store_gate_verdict(
        {
            "failure_owner": PRODUCT,
            "product_failed": failed,
            "product_failed_detail": {line.name: line.detail for line in report.lines if line.failed},
            "score": f"{report.passed}/{report.total}",
        }
    )


def _product_owned_check():
    """A floor check the product owns (derived, never named here)."""
    report = gate.report_from_store_gate_payload(_payload([]))
    return next(line.name for line in report.lines if line.owner == PRODUCT)


def test_the_artifact_is_looked_up_under_the_name_the_workflow_uploads():
    assert gate.store_gate_artifact_name(SHA) == f"{gate.STORE_GATE_ARTIFACT_NAME}-{SHA}"


def test_one_failing_check_is_one_item_carrying_its_own_id_and_brief_line():
    target = _product_owned_check()
    report = gate.report_from_store_gate_payload(_payload([target]))
    verdict = _verdict(report)
    assert verdict is not None
    assert [f.check_id for f in verdict.findings] == [target]
    item = str(verdict.findings[0])
    assert item.startswith(f"[{target}]")
    assert gate._what_to_build(target) in item
    others = [n for n in ACCEPTANCE_CHECK_NAMES if n != target and gate._what_to_build(n)]
    assert not any(gate._what_to_build(n) in item for n in others)


def test_two_failing_checks_are_two_items_each_with_its_own_id():
    report = gate.report_from_store_gate_payload(_payload([]))
    two = [line.name for line in report.lines if line.owner == PRODUCT][:2]
    report = gate.report_from_store_gate_payload(_payload(two))
    verdict = _verdict(report)
    assert [f.check_id for f in verdict.findings] == two
    for finding, name in zip(verdict.findings, two):
        assert str(finding).startswith(f"[{name}]")


def test_a_red_status_that_was_not_itemised_blames_no_check():
    snap = gate.StoreGateSnapshot(
        state="failure",
        description="acceptance.py in Docker 21/22 FAIL:audit_clean",
        passed=21,
        total=22,
        ok=False,
        lines=[],
    )
    report = gate.report_from_snapshot(snap)
    assert all(line.status == NOT_RUN for line in report.lines)
    assert not [line.name for line in report.lines if line.owner == PRODUCT and line.failed]
    assert _verdict(report) is None  # no product rework on a guess


def test_a_fail_name_the_floor_does_not_know_is_owed_by_the_factory():
    payload = _payload([])
    payload["lines"].append({"name": "zorblat_unknown_check", "status": "FAIL", "detail": "x"})
    report = gate.report_from_store_gate_payload(payload)
    assert "zorblat_unknown_check" in report.factory_owed
    assert not report.ok
    assert not [line.name for line in report.lines if line.owner == PRODUCT and line.failed]


def test_the_harness_carries_the_floors_audit_checks():
    """The gate attaches its security-scan findings to the lines the floor
    declares ``stage: audit`` -- read from the rendered harness, so the gate
    names no check itself."""
    import ast

    from app.factory.build.acceptance_floor import audit_check_ids
    from app.factory.build.store_acceptance import render_acceptance_script

    assert audit_check_ids(), "no check declares stage: audit"
    tree = ast.parse(render_acceptance_script())
    consts = {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in {"AUDIT_CHECKS", "CHECKS"}
    }
    assert consts["AUDIT_CHECKS"] == list(audit_check_ids())
    assert set(consts["AUDIT_CHECKS"]) <= set(consts["CHECKS"])


def test_a_floor_line_the_artifact_omitted_is_not_run_not_a_product_fail():
    payload = _payload([])
    dropped = payload["lines"].pop(0)["name"]
    report = gate.report_from_store_gate_payload(payload)
    line = next(line for line in report.lines if line.name == dropped)
    assert line.status == NOT_RUN and not line.failed
