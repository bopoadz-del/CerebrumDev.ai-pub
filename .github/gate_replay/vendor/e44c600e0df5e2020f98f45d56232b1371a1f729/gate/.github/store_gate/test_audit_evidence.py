"""The gate's audit verdict and the evidence it hands the writer share one rule."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import audit_evidence as ae  # noqa: E402

HARNESS = 'CHECKS = ["zorblat_audit", "other_check"]\nAUDIT_CHECKS = ["zorblat_audit"]\n'


def _report(tmp_path, results):
    p = tmp_path / "bandit.json"
    p.write_text(json.dumps({"results": results}), encoding="utf-8")
    return p


def _harness(tmp_path, text=HARNESS):
    p = tmp_path / "acceptance.py"
    p.write_text(text, encoding="utf-8")
    return p


SQL = {"test_id": "B608", "issue_severity": "MEDIUM", "filename": "app/handlers/x.py", "line_number": 42, "issue_text": "Possible SQL injection"}
HIGH = {"test_id": "B602", "issue_severity": "HIGH", "filename": "app/run.py", "line_number": 7, "issue_text": "shell=True"}
LOW = {"test_id": "B101", "issue_severity": "LOW", "filename": "app/a.py", "line_number": 1, "issue_text": "assert used"}


def test_verdict_is_clean_only_without_blocking_results(tmp_path):
    assert ae.verdict(_report(tmp_path, [LOW])) == "1"
    assert ae.verdict(_report(tmp_path, [LOW, SQL])) == "0"
    assert ae.verdict(_report(tmp_path, [HIGH])) == "0"
    assert ae.verdict(tmp_path / "missing.json") == "0"


def test_evidence_names_file_line_and_test_for_the_declared_audit_checks(tmp_path):
    found = ae.evidence(_harness(tmp_path), _report(tmp_path, [LOW, SQL, HIGH]))
    assert set(found) == {"zorblat_audit"}
    text = found["zorblat_audit"]
    assert "B608 app/handlers/x.py:42" in text and "B602 app/run.py:7" in text
    assert "B101" not in text  # not blocking -> not handed to the writer


def test_a_harness_without_audit_checks_gets_no_evidence(tmp_path):
    found = ae.evidence(_harness(tmp_path, 'CHECKS = ["a"]\n'), _report(tmp_path, [SQL]))
    assert found == {}


def test_main_writes_the_evidence_file(tmp_path):
    rc = ae.main(["evidence", str(_harness(tmp_path)), str(_report(tmp_path, [SQL])), str(tmp_path)])
    assert rc == 0
    data = json.loads((tmp_path / ae.OUT_NAME).read_text(encoding="utf-8"))
    assert "B608" in data["zorblat_audit"]


def _pip(tmp_path, deps, shape="object"):
    p = tmp_path / "pip_audit.json"
    p.write_text(json.dumps({"dependencies": deps} if shape == "object" else deps), encoding="utf-8")
    return p


VULN = {"name": "zorblatlib", "version": "1.2.3", "vulns": [{"id": "PYSEC-2099-1", "fix_versions": ["1.2.4"]}]}
SAFE = {"name": "quietlib", "version": "2.0", "vulns": []}


def test_a_pip_audit_vulnerability_blocks_the_verdict(tmp_path):
    clean = _report(tmp_path, [LOW])
    assert ae.verdict(clean, _pip(tmp_path, [SAFE])) == "1"
    assert ae.verdict(clean, _pip(tmp_path, [SAFE, VULN])) == "0"
    assert ae.verdict(clean, _pip(tmp_path, [VULN], shape="list")) == "0"
    # Named but unreadable: the scan did not prove the dependencies clean.
    assert ae.verdict(clean, tmp_path / "missing_pip.json") == "0"
    # Not named at all: bandit alone decides (a tree with no requirements).
    assert ae.verdict(clean) == "1"


def test_typed_rows_carry_file_line_and_package_version_as_fields(tmp_path):
    found = ae.evidence_rows(
        _harness(tmp_path), _report(tmp_path, [LOW, SQL]), _pip(tmp_path, [VULN])
    )
    rows = found["zorblat_audit"]
    assert {"kind": "file", "file": "app/handlers/x.py", "line": 42, "test_id": "B608",
            "severity": "MEDIUM", "text": "Possible SQL injection"} in rows
    assert {"kind": "dependency", "package": "zorblatlib", "version": "1.2.3",
            "vuln_id": "PYSEC-2099-1"} in rows
    assert all(r.get("test_id") != "B101" for r in rows)  # not blocking


def test_main_writes_typed_rows_beside_the_text(tmp_path):
    rc = ae.main([
        "evidence", str(_harness(tmp_path)), str(_report(tmp_path, [SQL])),
        str(tmp_path), str(_pip(tmp_path, [VULN])),
    ])
    assert rc == 0
    text = json.loads((tmp_path / ae.OUT_NAME).read_text(encoding="utf-8"))
    typed = json.loads((tmp_path / ae.ROWS_OUT_NAME).read_text(encoding="utf-8"))
    assert "PYSEC-2099-1 zorblatlib==1.2.3" in text["zorblat_audit"]
    kinds = {r["kind"] for r in typed["zorblat_audit"]}
    assert kinds == {"file", "dependency"}


def test_a_scan_is_measured_only_when_its_reports_exist_and_parse(tmp_path):
    report = _report(tmp_path, [SQL])
    assert ae.measured(report) == "1"
    assert ae.measured(tmp_path / "absent.json") == "0"
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert ae.measured(broken) == "0"
    # A named pip-audit report that is missing means pip-audit never ran.
    assert ae.measured(report, str(tmp_path / "no_pip.json")) == "0"
