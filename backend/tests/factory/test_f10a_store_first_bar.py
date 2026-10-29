"""1900Z Store-first bar for kernel definitions with no Store coverage.

A base definition the Store does not cover must carry the label
"improvised, no Store coverage", a published (not internal) provenance with a
non-empty reference, and a row in new_domain_candidates.jsonl naming its key.
The checker is generic over any definitions/candidates pair; the real-data
test then asserts the ruled definition is under the bar and passes it.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

FORMULAS = Path(__file__).resolve().parents[2] / "app" / "cerebrum_product_kernel" / "formulas"
DEFS = FORMULAS / "universal_definitions.json"
CANDIDATES = FORMULAS / "new_domain_candidates.jsonl"
IMPROVISED = "improvised, no Store coverage"
RULED_UNCOVERED = ("universal:contribution_margin_ratio_v1",)  # ORDERS 1900Z ruling


def store_first_errors(definitions: list, candidate_rows: list, uncovered) -> list:
    by_key = {d.get("key"): d for d in definitions}
    cand = {r.get("definition") for r in candidate_rows}
    errs = []
    for key in uncovered:
        d = by_key.get(key)
        if d is None:
            errs.append(f"{key}: not defined")
            continue
        if d.get("label") != IMPROVISED:
            errs.append(f"{key}: label {d.get('label')!r}")
        prov = d.get("provenance") or {}
        if prov.get("kind") != "published_reference":
            errs.append(f"{key}: provenance.kind {prov.get('kind')!r}")
        if not str(prov.get("reference") or "").strip():
            errs.append(f"{key}: empty reference")
        if key not in cand:
            errs.append(f"{key}: no new_domain_candidates row")
    # any definition labelled improvised must also be a logged candidate
    for d in definitions:
        if d.get("label") == IMPROVISED and d.get("key") not in cand:
            errs.append(f"{d.get('key')}: labelled improvised but not logged")
    return errs


def _rows(path: Path) -> list:
    if not path.is_file():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def test_ruled_uncovered_definitions_meet_the_store_first_bar():
    defs = json.loads(DEFS.read_text(encoding="utf-8"))["definitions"]
    assert store_first_errors(defs, _rows(CANDIDATES), RULED_UNCOVERED) == []


def test_candidate_rows_are_well_formed():
    rows = _rows(CANDIDATES)
    assert rows, "new_domain_candidates.jsonl missing or empty"
    for r in rows:
        assert r.get("reason") == "no_store_coverage" and r.get("label") == IMPROVISED
        assert r.get("sources") and all(str(s.get("reference", "")).strip() for s in r["sources"])


# ---- checker, two inputs each (synthetic keys) ----
def _d(key, **kw):
    base = {"key": key, "label": IMPROVISED,
            "provenance": {"kind": "published_reference", "reference": "Book, ch. 1"}}
    base.update(kw)
    return base


@pytest.mark.parametrize("defs, rows, expect", [
    ([_d("u:a_v1")], [{"definition": "u:a_v1"}], []),
    ([_d("u:b_v1")], [{"definition": "u:b_v1"}], []),
    ([_d("u:a_v1", label=None)], [{"definition": "u:a_v1"}], ["u:a_v1: label None"]),
    ([_d("u:a_v1", provenance={"kind": "internal_protocol", "reference": "x"})], [{"definition": "u:a_v1"}],
     ["u:a_v1: provenance.kind 'internal_protocol'"]),
    ([_d("u:a_v1", provenance={"kind": "published_reference", "reference": " "})], [{"definition": "u:a_v1"}],
     ["u:a_v1: empty reference"]),
    ([_d("u:a_v1")], [], ["u:a_v1: no new_domain_candidates row", "u:a_v1: labelled improvised but not logged"]),
    ([], [], ["u:a_v1: not defined"]),
])
def test_store_first_checker_two_inputs(defs, rows, expect):
    keys = ("u:a_v1",) if not defs or defs[0]["key"] == "u:a_v1" else ("u:b_v1",)
    assert store_first_errors(defs, rows, keys) == expect


def test_unruled_improvised_label_must_be_logged():
    assert store_first_errors([_d("u:c_v1")], [], ()) == ["u:c_v1: labelled improvised but not logged"]
    assert store_first_errors([_d("u:c_v1")], [{"definition": "u:c_v1"}], ()) == []
