"""Compiler end-to-end tests: fixture donor -> scout -> compile -> validate
-> package -> publish -> install. Also the RED checks: the compiler must
never self-certify, and a broken pack must refuse to package."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))

from app.domain_compiler.generator import CandidatePackGenerator  # noqa: E402
from app.domain_compiler.publisher import (  # noqa: E402
    PublishRefusedError,
    install,
    package,
    publish,
)
from app.domain_compiler.scout import DonorScout  # noqa: E402
from app.domain_compiler.validator import validate_pack  # noqa: E402


@pytest.fixture()
def donor_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "donor"
    (repo / "backend" / "app").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "backend" / "app" / "pricing.py").write_text(
        '"""Fixture pricing module."""\n'
        "from decimal import Decimal\n\n\n"
        "def calculate_retention(value, budget):\n"
        "    \"\"\"Retention % — the donor's known-answer oracle lives in tests.\"\"\"\n"
        "    return (Decimal(value) / Decimal(budget)).quantize(Decimal('0.0001'))\n\n\n"
        "def require_approval(amount, tier):\n"
        "    # minimum_approvals: 1 for low, 2 for high\n"
        "    # self_approval is never allowed\n"
        "    return tier == 'high'\n\n\n"
        "PAYMENT_RULES = [\n"
        "    {'rule_id': 'PAY-01', 'procedure': 'release only after sign-off',\n"
        "     'violation_message': 'PAY-01 violated'},\n"
        "]\n\n\n"
        "def next_payment_status(current, evidence):\n"
        "    # VALID_PAYMENT_STATES = draft, approved, released\n"
        "    return 'approved' if evidence else current\n",
        encoding="utf-8",
    )
    (repo / "backend" / "app" / "dup.py").write_text(
        '"""A second implementation of calculate_retention - duplicate."""\n'
        "def calculate_retention(value, budget):\n"
        "    return value / budget\n",
        encoding="utf-8",
    )
    (repo / "tests" / "test_pricing.py").write_text(
        "def test_calculate_retention_known_answer():\n"
        "    assert calculate_retention('50', '200') == ...\n",
        encoding="utf-8",
    )
    return repo


def test_scout_finds_kinds_with_evidence(donor_repo: Path):
    report = DonorScout(donor_repo, commit="abc1234").scout()
    kinds = {(d.kind, d.symbol) for d in report.discoveries}
    assert ("formula", "calculate_retention") in kinds
    assert ("approval", "require_approval") in kinds
    assert ("rule", "PAYMENT_RULES") in kinds
    assert ("workflow", "next_payment_status") in kinds
    # Both file locations of the duplicate symbol are discovered.
    locations = {d.path for d in report.discoveries if d.symbol == "calculate_retention"}
    assert "backend/app/pricing.py" in locations
    assert "backend/app/dup.py" in locations
    # The donor's test file is indexed as evidence.
    evidence = {
        tuple(d.test_evidence)
        for d in report.discoveries
        if d.symbol == "calculate_retention"
    }
    assert any("test_pricing.py" in item for e in evidence for item in e)
    # Every discovery carries the donor commit.
    assert all(d.donor_commit == "abc1234" for d in report.discoveries)


def test_scout_classifies_by_shape_not_vocabulary(tmp_path: Path):
    """Names, comments and docstrings carry no hint words: discovery comes
    from what the code returns and how its constants are built."""
    repo = tmp_path / "plain"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "m.py").write_text(
        "def f(a, b):\n    return a * b\n\n\n"
        "def g(x):\n    return x > 3\n\n\n"
        "def h(s, ok):\n    return 'b' if ok else s\n\n\n"
        "def j(x):\n    return str(x)\n\n\n"
        "TABLE = [{'k': 1, 'v': 2}, {'k': 3, 'v': 4}]\n"
        "lower = [{'k': 1, 'v': 2}]\n",
        encoding="utf-8",
    )
    kinds = {(d.kind, d.symbol) for d in DonorScout(repo).scout().discoveries}
    assert kinds == {
        ("formula", "f"),
        ("approval", "g"),
        ("workflow", "h"),
        ("rule", "TABLE"),
    }


def test_compile_never_certifies_and_provenance_is_complete(donor_repo: Path):
    report = DonorScout(donor_repo, commit="abc1234").scout()
    gen = CandidatePackGenerator(
        domain_id="fixture_ops",
        name="Fixture Operations",
        donor_repo="bopoadz-del/fixture-donor",
        donor_commit="abc1234",
    )
    pack = gen.compile(report)
    assert pack["manifest"]["certification"] == "candidate"
    artifacts = (
        pack["formulas"] + pack["rules"] + pack["workflows"] + pack["approvals"]
    )
    assert artifacts, "fixture donor should produce artifacts"
    for artifact in artifacts:
        assert artifact["certification"] == "candidate"
        prov = artifact["provenance"]
        for field in ("donor_repo", "donor_commit", "donor_path", "donor_symbol"):
            assert prov.get(field), f"{artifact} missing provenance {field}"
    # Both locations of the duplicated symbol become separate candidates.
    retention = [f for f in pack["formulas"] if f["name"] == "calculate_retention"]
    assert len(retention) == 2


def test_duplicates_and_contradictions_flagged_not_resolved(donor_repo: Path):
    report = DonorScout(donor_repo, commit="abc1234").scout()
    gen = CandidatePackGenerator("fixture_ops", "Fixture", "donor", "abc1234")
    report_dict = gen.compiler_report(report)
    assert report_dict["duplicates"], "duplicate symbol must be flagged"
    assert report_dict["contradictions"], "two implementations must be flagged"
    dup = report_dict["duplicates"][0]
    assert dup["symbol"] == "calculate_retention"
    assert len(dup["locations"]) == 2
    contradiction = report_dict["contradictions"][0]
    assert contradiction["resolution"] == "domain_review_required"


def test_missing_test_templates_and_role_drafts(donor_repo: Path):
    report = DonorScout(donor_repo, commit="abc1234").scout()
    gen = CandidatePackGenerator("fixture_ops", "Fixture", "donor", "abc1234")
    pack = gen.compile(report)
    templates = pack["tests"]["missing_test_templates"]
    assert templates, "unverified artifacts need missing-test templates"
    assert any("require_approval" in name for name in templates)
    drafts = pack["tests"]["role_matrix_drafts"]
    assert any(d["action"] == "require_approval" for d in drafts)
    assert all(
        d["risk_tier"] == "domain_review_required" for d in drafts
    )
    assert pack["tests"]["workflow_diagrams"]["next_payment_status"].startswith(
        "stateDiagram-v2"
    )
    assert "calculate_retention" in pack["verification_oracles"]
    # Oracles are generated for formula-kind discoveries only.


def test_validate_passes_compiler_output(donor_repo: Path):
    report = DonorScout(donor_repo, commit="abc1234").scout()
    gen = CandidatePackGenerator("fixture_ops", "Fixture", "donor", "abc1234")
    pack = gen.compile(report)
    ok, reasons = validate_pack(pack)
    assert ok, reasons


def test_validate_rejects_self_certification(donor_repo: Path):
    report = DonorScout(donor_repo, commit="abc1234").scout()
    gen = CandidatePackGenerator("fixture_ops", "Fixture", "donor", "abc1234")
    pack = gen.compile(report)
    pack["formulas"][0]["certification"] = "domain_approved"
    ok, reasons = validate_pack(pack)
    assert not ok
    assert any("non-candidate" in r for r in reasons)


def test_package_publish_install_roundtrip(donor_repo: Path, tmp_path: Path):
    report = DonorScout(donor_repo, commit="abc1234").scout()
    gen = CandidatePackGenerator("fixture_ops", "Fixture", "donor", "abc1234")
    pack = gen.compile(report)
    compiler_report = gen.compiler_report(report)
    out_dir = tmp_path / "out"
    pack_path = package(pack, compiler_report, out_dir)
    assert pack_path.name == "pack.json"
    assert pack_path.parent.name == "fixture_ops"
    assert (pack_path.parent / "compiler_report.json").is_file()
    # publish into a store-shaped destination
    store_root = tmp_path / "store"
    published = publish(pack_path, store_root)
    assert published == store_root / "domain_packs" / "fixture_ops" / "pack.json"
    assert published.is_file()
    # install into a product-shaped tree
    product = tmp_path / "product"
    installed = install(pack_path, product)
    assert installed == product / "app" / "domain_packs" / "fixture_ops" / "pack.json"
    note = product / "app" / "domain_packs" / "fixture_ops" / "INSTALL_NOTE.md"
    assert note.is_file()
    assert "candidate" in note.read_text(encoding="utf-8")
    # round-trip integrity: the packaged pack parses and still validates
    roundtrip = json.loads(published.read_text(encoding="utf-8"))
    ok, reasons = validate_pack(roundtrip)
    assert ok, reasons


def test_package_refuses_invalid_pack(donor_repo: Path, tmp_path: Path):
    report = DonorScout(donor_repo, commit="abc1234").scout()
    gen = CandidatePackGenerator("fixture_ops", "Fixture", "donor", "abc1234")
    pack = gen.compile(report)
    pack["manifest"]["version"] = "not-a-version"
    with pytest.raises(PublishRefusedError):
        package(pack, gen.compiler_report(report), tmp_path / "out")


def test_cli_scout_compile_validate(tmp_path: Path, donor_repo: Path):
    import subprocess

    venv_python = BACKEND / ".venv-factory" / "Scripts" / "python.exe"
    if not venv_python.is_file():
        pytest.skip(reason="factory venv not present")
    report_out = tmp_path / "scout.json"
    proc = subprocess.run(
        [
            str(venv_python),
            "-m",
            "app.domain_compiler.cli",
            "scout",
            "--repo",
            str(donor_repo),
            "--commit",
            "abc1234",
            "--out",
            str(report_out),
        ],
        cwd=str(BACKEND),
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, proc.stderr
    assert report_out.is_file()
    pack_dir = tmp_path / "packed"
    proc = subprocess.run(
        [
            str(venv_python),
            "-m",
            "app.domain_compiler.cli",
            "compile",
            "--report",
            str(report_out),
            "--domain-id",
            "fixture_ops",
            "--name",
            "Fixture Ops",
            "--out",
            str(pack_dir),
        ],
        cwd=str(BACKEND),
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, proc.stderr
    pack_path = pack_dir / "fixture_ops" / "pack.json"
    assert pack_path.is_file()
    proc = subprocess.run(
        [
            str(venv_python),
            "-m",
            "app.domain_compiler.cli",
            "validate",
            "--pack",
            str(pack_path),
        ],
        cwd=str(BACKEND),
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, proc.stderr
    assert "PASS" in proc.stdout
