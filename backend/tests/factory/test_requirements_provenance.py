"""Every requirement line the Factory renders is the Factory's, on the receipt.

Live 2026-10-07 (e9efdf77, smoke A, store-gate run 37637946049, branch
build/plt_9f7fac60d89e4a80): ``audit_clean`` FAILED on 33 pip-audit rows, all
``pillow==10.4.0`` -- no bandit rows. The receipt billed every one to the
writer ("a dependency the writer added to requirements.txt"); the runner
reworked, saw the same failure twice and STOPPED.

Nobody on the writer side declared pillow. The branch of record carried four
UNMARKED blocks the pre-#683 Factory appended to requirements.txt -- headed
"(refreshed by the factory)." -- naming ``Pillow`` (``ocr_v2.py``) and
``marker-pdf`` (which pins ``pillow<11``) for blocks this build no longer
vendors. #683 moved the Factory's lines into a marked block, but the marker
reader saw the old blocks as product bytes, so the stale Factory lines stayed
for ever and the receipt (base render only) called them the writer's.

The mechanism, not the package: the Factory's own stamp removes the blocks
its earlier self emitted (recognised by that emitter's header, a Factory
constant), and the receipt records every line the Factory's block declares.
"""

from __future__ import annotations

from pathlib import Path

from app.factory.build import factory_receipt as fr
from app.factory.build.acceptance_floor import FACTORY, PRODUCT, audit_check_ids
from app.factory.build.factory_block import apply_block
from app.factory.build.factory_refresh import _dist, merged_requirements, refresh_factory_files
from app.factory.build.n3_store_gate import report_from_store_gate_payload, split_audit_by_origin
from app.factory.build.roles_handlers import _render_requirements
from app.factory.build.store_acceptance import ACCEPTANCE_CHECK_NAMES

AUDIT = audit_check_ids()[0]

#: The bytes the pre-#683 Factory appended, exactly as they sit on the live
#: branch (a recorded tree, not a rule: the code reads its own constant).
LIVE_LEGACY_BLOCKS = (
    "\n"
    "# Packages this tree needs and did not declare: vendored block\n"
    "# imports, and framework features FastAPI does not declare\n"
    "# (refreshed by the factory).\n"
    "Pillow  # module: PIL in ocr_v2.py, redline.py\n"
    "easyocr  # action: easyocr in ocr_v2.py\n"
    "\n"
    "# Packages this tree needs and did not declare: vendored block\n"
    "# imports, and framework features FastAPI does not declare\n"
    "# (refreshed by the factory).\n"
    "marker-pdf  # action: marker in pdf.py\n"
    "pytesseract  # action: pytesseract in pdf.py\n"
)

#: A section the WRITER wrote above the Factory's lines (live, numpy & co).
WRITER_SECTION = "\n# Imported by the blocks this platform calls.\nzorblat-sdk>=1.0\n"


def _dists(text: str) -> set:
    return {d for d in (_dist(line) for line in text.splitlines()) if d}


def _live_tree(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    (root / "app").mkdir(parents=True)
    (root / "app" / "zorblat_handler.py").write_text("def handle(p):\n    return p\n", encoding="utf-8")
    base = _render_requirements({}, root=root)
    # bytes, not text: the live branch is LF and the product bytes are the claim
    (root / "requirements.txt").write_bytes((base + WRITER_SECTION + LIVE_LEGACY_BLOCKS).encode("utf-8"))
    return root


def _pillow_rows(n: int = 33) -> list:
    return [
        {"kind": "dependency", "package": "pillow", "version": "10.4.0", "vuln_id": f"PYSEC-2026-{i}"}
        for i in range(n)
    ]


def _report(rows):
    lines = [
        {"name": n, "status": "FAIL", "detail": "unclean", "evidence_rows": rows}
        if n == AUDIT else {"name": n, "status": "PASS", "detail": "ok"}
        for n in ACCEPTANCE_CHECK_NAMES
    ]
    return report_from_store_gate_payload(
        {"lines": lines, "passed": len(lines) - 1, "total": len(lines), "ok": False}
    )


def _line(report):
    return next(line for line in report.lines if line.name == AUDIT)


def test_live_smoke_a_pillow_rows_are_never_the_writers(tmp_path):
    root = _live_tree(tmp_path)
    refresh_factory_files(root, "platform")  # the stamp TESTER runs before every round
    fr.record_receipt(root)
    report = _report(_pillow_rows())

    advisory = split_audit_by_origin(root, report)

    line = _line(report)
    assert line.owner == FACTORY and line.satisfied, line.evidence
    assert advisory and len(advisory[0]["findings"]) == 33
    assert report.ok


def test_the_factorys_stale_legacy_lines_are_removed_by_its_own_stamp(tmp_path):
    root = _live_tree(tmp_path)

    refresh_factory_files(root, "platform")

    declared = _dists((root / "requirements.txt").read_text(encoding="utf-8"))
    # nothing in the tree imports these any more: the Factory's own old lines go
    assert not declared & {"pillow", "easyocr", "marker-pdf", "pytesseract"}


def test_removing_legacy_blocks_leaves_every_product_byte(tmp_path):
    root = _live_tree(tmp_path)
    before = (root / "requirements.txt").read_bytes().decode("utf-8")
    product = before[: before.index(LIVE_LEGACY_BLOCKS)]

    text = merged_requirements(root)

    assert text.startswith(product)
    assert WRITER_SECTION in text
    assert "zorblat-sdk" in _dists(text)


def test_a_writer_line_after_a_legacy_block_survives(tmp_path):
    root = _live_tree(tmp_path)
    path = root / "requirements.txt"
    path.write_text(path.read_text(encoding="utf-8") + "\nzorblat-extra==2.0\n", encoding="utf-8")

    assert "zorblat-extra" in _dists(merged_requirements(root))


def test_the_stamp_is_idempotent_after_the_migration(tmp_path):
    root = _live_tree(tmp_path)
    refresh_factory_files(root, "platform")
    once = (root / "requirements.txt").read_bytes()

    assert refresh_factory_files(root, "platform") == []
    assert (root / "requirements.txt").read_bytes() == once


def test_every_line_in_the_factory_block_is_recorded_factory_owned(tmp_path):
    """The Factory block is what the Factory wrote at stamp time; a line in it
    is the Factory's even when today's render no longer lists it."""
    root = _live_tree(tmp_path)
    path = root / "requirements.txt"
    path.write_text(
        apply_block(path.read_text(encoding="utf-8"), "zorblat-imaging  # action: zimg in z.py\n"),
        encoding="utf-8",
    )

    receipt = fr.record_receipt(root)

    assert "zorblat-imaging" in receipt["base_requirements"]
    origin = fr.load_receipt(root).dependency_origin("zorblat-imaging")
    assert origin.owner == fr.FACTORY


def test_a_transitive_of_a_factory_dependency_is_the_factorys(tmp_path):
    """pillow arrives as marker-pdf's pin: undeclared, so never the writer's."""
    root = _live_tree(tmp_path)
    refresh_factory_files(root, "platform")
    fr.record_receipt(root)

    assert fr.load_receipt(root).dependency_origin("pillow").owner == fr.FACTORY


def test_pillow_the_writer_declared_is_still_the_writers(tmp_path):
    """The other direction: a dependency the WRITER put in its own lines stays
    rework, with the package==version rows handed back."""
    root = _live_tree(tmp_path)
    path = root / "requirements.txt"
    path.write_text(
        path.read_text(encoding="utf-8").replace(WRITER_SECTION, WRITER_SECTION + "Pillow>=10\n"),
        encoding="utf-8",
    )
    refresh_factory_files(root, "platform")
    fr.record_receipt(root)
    report = _report(_pillow_rows(2))

    assert split_audit_by_origin(root, report) == []
    line = _line(report)
    assert line.failed and line.owner == PRODUCT
    assert "pillow==10.4.0" in line.evidence
