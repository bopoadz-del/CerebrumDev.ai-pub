"""audit_clean ownership by line origin (owner decision 4).

A bandit / pip-audit finding in a file or dependency the WRITER authored goes
to the writer with file:line; one in Factory substrate (stamped files, vendored
Store code, the Factory's base requirements) is the Factory's and advisory for
the build -- the build is not failed for it. Provenance comes from the build's
factory receipt (factory_receipt.py), never from a filename; a finding whose
origin cannot be established is a Factory fault, never silently the writer's.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.factory.build import factory_receipt as fr
from app.factory.build.acceptance_floor import FACTORY, PRODUCT, audit_check_ids
from app.factory.build.brief_gates import advisory_checks
from app.factory.build.ledger import BuildLedger, EventKind
from app.factory.build.n3_store_gate import (
    StoreGateSnapshot,
    apply_store_gate_failure,
    apply_store_gate_success,
    report_from_store_gate_payload,
    split_audit_by_origin,
    store_gate_verdict,
)
from app.factory.build.store_acceptance import ACCEPTANCE_CHECK_NAMES, factory_rendered_paths

AUDIT = audit_check_ids()[0]


def _factory_file() -> str:
    """A path the Factory renders that lives under app/ (what bandit scans)."""
    rendered = [
        rel for rel in factory_rendered_paths() if rel.startswith("app/") and rel.endswith(".py")
    ]
    # The Factory always stamps runtime modules under app/ (tenancy, observe,
    # ...); none would be a Factory defect, so this fails rather than skips.
    assert rendered, "factory_rendered_paths() lists no app/ module"
    return rendered[0]


def _tree(tmp_path: Path, *, writer_deps=("zorblat-sdk",)) -> Path:
    root = tmp_path / "ws"
    stamped = root / _factory_file()
    stamped.parent.mkdir(parents=True, exist_ok=True)
    stamped.write_text("# stamped by the Factory\nX = 1\n", encoding="utf-8")
    writer = root / "app" / "zorblat_handler.py"
    writer.write_text("def handle(p):\n    return p\n", encoding="utf-8")
    base = sorted(fr.base_requirement_dists(root))
    (root / "requirements.txt").write_text(
        "\n".join(list(base) + list(writer_deps)) + "\n", encoding="utf-8"
    )
    return root


def _file_row(path: str, line: int = 12) -> dict:
    return {"kind": "file", "file": path, "line": line, "test_id": "B608",
            "severity": "MEDIUM", "text": "Possible SQL injection"}


def _dep_row(package: str) -> dict:
    return {"kind": "dependency", "package": package, "version": "1.0", "vuln_id": "PYSEC-2099-7"}


def _report(rows):
    lines = [
        {"name": n, "status": "FAIL", "detail": "unclean", "evidence_rows": rows}
        if n == AUDIT else {"name": n, "status": "PASS", "detail": "ok"}
        for n in ACCEPTANCE_CHECK_NAMES
    ]
    return report_from_store_gate_payload(
        {"lines": lines, "passed": len(lines) - 1, "total": len(lines), "ok": False}
    )


def _snap(report) -> StoreGateSnapshot:
    return StoreGateSnapshot(
        state="failure", missing=False, sha="a" * 40, branch="build/plt_zorblat",
        passed=report.passed, total=report.total, lines=list(report.lines),
    )


def _line(report):
    return next(line for line in report.lines if line.name == AUDIT)


def test_bandit_finding_in_a_writer_authored_file_goes_to_the_writer_with_file_line(tmp_path):
    root = _tree(tmp_path)
    fr.record_receipt(root)
    report = _report([_file_row("app/zorblat_handler.py", 12)])

    advisory = split_audit_by_origin(root, report)

    assert advisory == []
    line = _line(report)
    assert line.failed and line.owner == PRODUCT
    apply_store_gate_failure(root, _snap(report), honesty="N3_STORE_GATE_RED", report=report)
    terminal = BuildLedger(root / "build_ledger.jsonl").terminal_event()
    verdict = store_gate_verdict(terminal.payload)
    assert verdict is not None
    assert any("app/zorblat_handler.py:12" in str(f) for f in verdict.findings)


def test_bandit_finding_in_a_factory_stamped_file_is_advisory_and_does_not_fail(tmp_path):
    root = _tree(tmp_path)
    fr.record_receipt(root)
    report = _report([_file_row(_factory_file(), 2)])

    advisory = split_audit_by_origin(root, report)

    assert [a["check"] for a in advisory] == [AUDIT]
    assert fr.REASON_FACTORY_FILE in advisory[0]["reason"]
    line = _line(report)
    assert line.satisfied and line.owner == FACTORY
    assert report.ok  # red only on Factory substrate: the build is not failed
    from app.factory.build.n3_store_gate import record_audit_advisory

    record_audit_advisory(root, advisory)
    apply_store_gate_success(root, _snap(report), report=report)
    ledger = BuildLedger(root / "build_ledger.jsonl")
    assert ledger.terminal_event().kind is EventKind.RUN_SUCCEEDED
    assert [a["check"] for a in advisory_checks(ledger.events())] == [AUDIT]


def test_pip_audit_vuln_in_a_factory_base_requirement_is_advisory(tmp_path):
    root = _tree(tmp_path)
    receipt = fr.record_receipt(root)
    base = receipt["base_requirements"]
    assert base, "the Factory declares base requirements for any tree"
    report = _report([_dep_row(base[0])])

    advisory = split_audit_by_origin(root, report)

    assert advisory and fr.REASON_FACTORY_DEPENDENCY in advisory[0]["reason"]
    assert _line(report).satisfied and report.ok


def test_pip_audit_vuln_in_a_writer_added_dependency_is_rework(tmp_path):
    root = _tree(tmp_path, writer_deps=("zorblat-sdk",))
    fr.record_receipt(root)
    report = _report([_dep_row("zorblat-sdk")])

    advisory = split_audit_by_origin(root, report)

    assert advisory == []
    line = _line(report)
    assert line.failed and line.owner == PRODUCT
    assert "PYSEC-2099-7 zorblat-sdk==1.0" in line.evidence


def test_mixed_findings_split_never_relabel(tmp_path):
    root = _tree(tmp_path)
    fr.record_receipt(root)
    writer_row = _file_row("app/zorblat_handler.py", 12)
    factory_row = _file_row(_factory_file(), 2)
    report = _report([writer_row, factory_row])

    advisory = split_audit_by_origin(root, report)

    line = _line(report)
    assert line.failed and line.owner == PRODUCT
    assert line.evidence_rows == [writer_row]  # only the writer's row stays
    assert advisory[0]["findings"] and _factory_file() in advisory[0]["findings"][0]
    assert not report.ok


def test_no_receipt_is_a_factory_fault_never_the_writers(tmp_path):
    root = _tree(tmp_path)  # no record_receipt: a build from before receipts
    report = _report([_file_row("app/zorblat_handler.py", 12)])

    advisory = split_audit_by_origin(root, report)

    assert advisory and advisory[0]["reason"] == fr.REASON_NO_RECEIPT
    assert _line(report).owner == FACTORY and _line(report).satisfied


def test_a_factory_file_changed_after_its_stamp_is_a_factory_fault(tmp_path):
    root = _tree(tmp_path)
    fr.record_receipt(root)
    (root / _factory_file()).write_text("# edited after the stamp\nY = 2\n", encoding="utf-8")
    report = _report([_file_row(_factory_file(), 2)])

    advisory = split_audit_by_origin(root, report)

    assert advisory and fr.REASON_FACTORY_FILE_CHANGED in advisory[0]["reason"]


def test_origin_is_provenance_not_the_filename(tmp_path):
    """A writer file that shares a stamped file's NAME in another directory is
    the writer's; the stamped file's own path is the Factory's."""
    root = _tree(tmp_path)
    stamped = Path(_factory_file())
    lookalike = Path("app") / "zorblat_pkg" / stamped.name
    (root / lookalike).parent.mkdir(parents=True, exist_ok=True)
    (root / lookalike).write_text("def f():\n    return 1\n", encoding="utf-8")
    receipt = fr.load_receipt(root)
    assert receipt is None
    fr.record_receipt(root)
    receipt = fr.load_receipt(root)
    assert receipt.file_origin(lookalike.as_posix()).owner == fr.WRITER
    assert receipt.file_origin(stamped.as_posix()).owner == fr.FACTORY


def test_a_finding_naming_a_file_not_in_the_build_is_a_factory_fault(tmp_path):
    root = _tree(tmp_path)
    fr.record_receipt(root)
    origin = fr.load_receipt(root).file_origin("app/never_written.py")
    assert origin.owner == fr.FACTORY and origin.reason == fr.REASON_NOT_IN_TREE


def test_an_audit_line_without_typed_rows_is_advisory_not_the_writers(tmp_path):
    root = _tree(tmp_path)
    fr.record_receipt(root)
    report = _report([])

    advisory = split_audit_by_origin(root, report)

    assert advisory and _line(report).owner == FACTORY


def test_vendored_store_code_is_the_factorys(tmp_path):
    root = _tree(tmp_path)
    (root / "blocks.lock.json").write_text(
        json.dumps({"blocks": {"zorblat_block": {"path": "vendor/blocks/zorblat_block"}},
                    "runtime": {"path": "vendor/cerebrum", "files": ["vendor/cerebrum/core/x.py"]}}),
        encoding="utf-8",
    )
    fr.record_receipt(root)
    receipt = fr.load_receipt(root)
    assert receipt.file_origin("vendor/blocks/zorblat_block/block.py").owner == fr.FACTORY
    assert receipt.file_origin("vendor/cerebrum/core/x.py").owner == fr.FACTORY


def _ingest(root, rows):
    from app.factory.build.n3_store_gate import ingest_store_gate_snapshot

    report = _report(rows)
    snap = _snap(report)
    snap.lines = [line for line in report.lines]
    return ingest_store_gate_snapshot(root, snap)


def test_a_store_verdict_red_only_on_factory_audit_rows_passes_with_the_advisory_recorded(tmp_path):
    from app.factory.build.n3_store_gate import N3_STORE_GATE_GREEN

    root = _tree(tmp_path)
    receipt = fr.record_receipt(root)
    rows = [_file_row(_factory_file(), 2), _dep_row(receipt["base_requirements"][0])]

    result = _ingest(root, rows)

    assert result.ok and result.honesty == N3_STORE_GATE_GREEN
    ledger = BuildLedger(root / "build_ledger.jsonl")
    assert ledger.terminal_event().kind is EventKind.RUN_SUCCEEDED
    [row] = advisory_checks(ledger.events())
    assert row["check"] == AUDIT and row["findings_count"] == 2
    # the evidence rows themselves travel with the advisory, not just a count
    assert [f for f in row["findings"] if _factory_file() in f]
    assert [f for f in row["findings"] if "PYSEC-2099-7" in f]


def test_a_store_verdict_with_a_writer_audit_row_is_rework_never_factory_owed(tmp_path):
    """Live 2026-10-07 (b305c718, store-gate run 37617593610): every audit
    row was the writer's by the receipt, the line moved to PRODUCT, and the
    report kept its pre-split score -- "product passed 18/18 ... the Factory
    failed audit_clean" -- so a product finding was billed to the Factory and
    the build died with no rework."""
    from app.factory.build.n3_store_gate import N3_STORE_GATE_FACTORY_OWED

    root = _tree(tmp_path)
    fr.record_receipt(root)

    result = _ingest(root, [_file_row("app/zorblat_handler.py", 12), _dep_row("zorblat-sdk")])

    assert not result.ok and result.honesty != N3_STORE_GATE_FACTORY_OWED
    terminal = BuildLedger(root / "build_ledger.jsonl").terminal_event()
    assert terminal.payload.get("failure_owner") == PRODUCT
    verdict = store_gate_verdict(terminal.payload)
    assert verdict is not None
    assert any("app/zorblat_handler.py:12" in str(f) for f in verdict.findings)


def test_a_mixed_audit_line_is_rework_on_the_writer_rows_with_the_factory_rows_advisory(tmp_path):
    root = _tree(tmp_path)
    fr.record_receipt(root)

    result = _ingest(root, [_file_row("app/zorblat_handler.py", 12), _file_row(_factory_file(), 2)])

    assert not result.ok
    ledger = BuildLedger(root / "build_ledger.jsonl")
    assert ledger.terminal_event().payload.get("failure_owner") == PRODUCT
    assert [r["check"] for r in advisory_checks(ledger.events())] == [AUDIT]


def test_a_factory_owned_non_audit_failure_still_routes_to_the_factory_lane(tmp_path):
    """Scope: only audit lines are advisory. Any other Factory-owned red line
    keeps today's verdict (N3_STORE_GATE_FACTORY_OWED, no rework)."""
    from app.factory.build.acceptance_floor import owner_of
    from app.factory.build.n3_store_gate import (
        N3_STORE_GATE_FACTORY_OWED,
        ingest_store_gate_snapshot,
    )

    owned = [c for c in ACCEPTANCE_CHECK_NAMES if c not in audit_check_ids() and owner_of(c) == FACTORY]
    assert owned, "the floor declares a Factory-owned check"
    lines = [
        {"name": n, "status": "FAIL" if n == owned[0] else "PASS", "detail": "x"}
        for n in ACCEPTANCE_CHECK_NAMES
    ]
    report = report_from_store_gate_payload(
        {"lines": lines, "passed": len(lines) - 1, "total": len(lines), "ok": False}
    )
    root = _tree(tmp_path)
    fr.record_receipt(root)

    result = ingest_store_gate_snapshot(root, _snap(report))

    assert result.honesty == N3_STORE_GATE_FACTORY_OWED and not result.ok


def test_the_manifest_carries_the_advisory_evidence_rows():
    from app.factory.build import export_manifest as em

    manifest = em.build_manifest(
        product_id="p", tenant_id="t", ci_run="1",
        retrieval_mode=em.RETRIEVAL_MODES[0], tenancy_mode=em.TENANCY_MODES[0],
        embedder="e", vector_store="v", engine_version="1", prompt_version="1",
        layer_counts={1: 0}, engine_included=False,
        advisory_checks=[{"check": AUDIT, "reason": "r", "findings_count": 1,
                          "findings": ["PYSEC-2099-7 zorblat==1.0"]}],
    )
    assert manifest["advisory_checks"][0]["findings"] == ["PYSEC-2099-7 zorblat==1.0"]


def test_the_receipt_is_written_at_the_store_gate_handoff():
    """The runner records the receipt before it pushes the workspace for the
    gate -- after the last restamp -- and the receipt never ships."""
    import inspect

    from app.factory.build import runner
    from app.factory.build.builds_push import FACTORY_INTERNAL_PATHS

    src = inspect.getsource(runner)
    handoff = src.index("dispatch_store_gate(gate_branch")
    assert src.rfind("record_receipt(self.workspace)", 0, handoff) != -1
    assert fr.RECEIPT_REL in FACTORY_INTERNAL_PATHS
