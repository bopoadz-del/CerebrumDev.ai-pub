"""Two-input tests for the realistic pilot fixture generator (item 2b).

Each decision is exercised with two distinct seeds. The fixtures are synthetic:
no real company names and no owner data.
"""

from __future__ import annotations

import pytest
from pypdf import PdfReader

from generate_fixtures import (
    SEED,
    generate_boq,
    generate_contract,
)

_REAL_COMPANY_NAMES = [
    "Arup",
    "Atkins",
    "Balfour Beatty",
    "Bechtel",
    "Fluor",
    "Laing O'Rourke",
    "Mace",
    "Skanska",
    "Siemens",
    "Vinci",
]


@pytest.mark.parametrize("seed", [SEED, SEED + 1])
def test_contract_page_count_in_range(tmp_path, seed):
    path = generate_contract(tmp_path / "contract.pdf", seed)
    pages = len(PdfReader(path).pages)
    assert 40 <= pages <= 80


@pytest.mark.parametrize("seed", [SEED, SEED + 1])
def test_boq_line_count_in_range(tmp_path, seed):
    path = generate_boq(tmp_path / "boq.xlsx", seed)
    import openpyxl

    wb = openpyxl.load_workbook(path)
    ws = wb["Bill of Quantities"]
    assert 300 <= ws.max_row <= 800
    assert "Summary" in wb.sheetnames


@pytest.mark.parametrize("seed", [SEED, SEED + 1])
def test_contract_carries_the_grader_checked_clauses(tmp_path, seed):
    path = generate_contract(tmp_path / "contract.pdf", seed)
    text = "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
    for clause in ("4.2", "8.2", "8.7", "11.1", "14.3"):
        assert clause in text


@pytest.mark.parametrize("seed", [SEED, SEED + 1])
def test_contract_carries_contract_data(tmp_path, seed):
    path = generate_contract(tmp_path / "contract.pdf", seed)
    text = "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
    for term in (
        "Time for Completion",
        "Delay Damages",
        "Defects Notification Period",
        "Engineer",
        "Performance Security",
        "Milestone",
    ):
        assert term in text


@pytest.mark.parametrize("seed", [SEED, SEED + 1])
def test_regeneration_is_deterministic(tmp_path, seed):
    a = generate_contract(tmp_path / "a.pdf", seed).read_bytes()
    b = generate_contract(tmp_path / "b.pdf", seed).read_bytes()
    assert a == b
    x = generate_boq(tmp_path / "a.xlsx", seed).read_bytes()
    y = generate_boq(tmp_path / "b.xlsx", seed).read_bytes()
    assert x == y


@pytest.mark.parametrize("seed", [SEED, SEED + 1])
def test_fixtures_carry_no_real_company_names(tmp_path, seed):
    path = generate_contract(tmp_path / "contract.pdf", seed)
    text = "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
    for name in _REAL_COMPANY_NAMES:
        assert name.lower() not in text.lower()
