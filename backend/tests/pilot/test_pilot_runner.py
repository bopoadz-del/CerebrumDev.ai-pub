"""Unit tests for scripts/pilot_runner.py pure stage checks.

No network: each check is a pure function over the live API's JSON shapes.
Two distinct literal cases per assertion so a mutant that flips the verdict is
killed and the two-input lint is satisfied.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]  # backend/tests/pilot/<file> -> repo root
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

pilot = pytest.importorskip("pilot_runner")

FILES = ["harbourline_contract.pdf", "harbourline_boq.xlsx"]


@pytest.mark.parametrize(
    "result, expected",
    [
        ({"status": "completed", "total_chunks": 12, "indexed_collection": "c1", "failed_files": []}, True),
        ({"status": "completed_with_warnings", "total_chunks": 3, "indexed_collection": "c1", "failed_files": []}, True),
        ({"status": "completed", "total_chunks": 0, "indexed_collection": "c1", "failed_files": []}, False),
        ({"status": "completed", "total_chunks": 9, "indexed_collection": "", "failed_files": []}, False),
        ({"status": "failed", "total_chunks": 9, "indexed_collection": "c1", "failed_files": ["x"]}, False),
    ],
)
def test_upload_indexed(result, expected):
    ok, _ev = pilot.upload_indexed(result, set(FILES))
    assert ok is expected


@pytest.mark.parametrize(
    "product, expected",
    [
        ({"brief": "req from document:harbourline_contract.pdf and document:harbourline_boq.xlsx", "blueprint": {}}, True),
        ({"brief": "", "blueprint": {"notes": "document:harbourline_contract.pdf document:harbourline_boq.xlsx"}}, True),
        ({"brief": "only document:harbourline_contract.pdf", "blueprint": {}}, False),
        ({"brief": "no citations at all", "blueprint": {}}, False),
    ],
)
def test_brief_cites_documents(product, expected):
    ok, _ev = pilot.brief_cites_documents(product, FILES)
    assert ok is expected


@pytest.mark.parametrize(
    "build, zip_ok, expected",
    [
        ({"state": "converged", "phases_done": 5, "phases_total": 5}, True, True),
        ({"state": "complete", "phases_done": 5, "phases_total": 5}, False, False),
        ({"state": "working", "phases_done": 3, "phases_total": 5}, True, False),
        ({"state": "failed", "phases_done": 5, "phases_total": 5}, True, False),
    ],
)
def test_build_reached_store(build, zip_ok, expected):
    ok, _ev = pilot.build_reached_store(build, zip_ok)
    assert ok is expected


_FINDING = "documents were not indexed into the product corpus: retrieval backend down"


@pytest.mark.parametrize(
    "upload, ledger, expected",
    [
        ({"message": "", "failed_files": []}, [], True),
        ({"message": "", "failed_files": []}, [{"payload": {"other": 1}}], True),
        ({"message": _FINDING, "failed_files": [_FINDING]}, [], False),
        ({"message": "", "failed_files": []}, [{"payload": {"corpus_seed_finding": "no tenant"}}], False),
    ],
)
def test_corpus_seed_ok(upload, ledger, expected):
    ok, _ev = pilot.corpus_seed_ok(upload, ledger)
    assert ok is expected


@pytest.mark.parametrize(
    "first, second, expected",
    [
        ({"generation": {"output_dir": "/ws/p1"}}, {"generation": {"output_dir": "/ws/p1"}, "already_complete": True}, True),
        ({"generation": {"output_dir": "/ws/p1"}}, {"generation": {"output_dir": "/ws/p1"}, "resumed": True}, True),
        ({"generation": {"output_dir": "/ws/p1"}}, {"generation": {"output_dir": "/ws/p2"}, "resumed": True}, False),
        ({"generation": {"output_dir": "/ws/p1"}}, {"generation": {"output_dir": "/ws/p1"}}, False),
    ],
)
def test_resume_not_restart(first, second, expected):
    ok, _ev = pilot.resume_not_restart(first, second)
    assert ok is expected
