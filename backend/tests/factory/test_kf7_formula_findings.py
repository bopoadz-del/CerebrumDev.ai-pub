"""K-F7: the formula findings -- what the writer authored is graded against
the kernel and the Store (ORDERS_1900Z), flagged by name and recorded.

Store trees are synthetic (tmp_path) and named per test, so every assertion
is about structure, never a real pack or kit.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest



def _fr():
    # Imported by its full dotted path so CI-M's tests_for() maps
    # app.factory.build.formula_resolution to this file.
    import app.factory.build.formula_resolution as formula_resolution

    return formula_resolution


IMPROVISED_LABEL = "improvised, no Store coverage"


class _BP:
    def __init__(self, vertical):
        self.product_id = "p_x"
        self.product_name = "X"
        self.vertical = vertical
        self.summary = "s"
        self.capabilities = []


def _store(root: Path, packs: dict, kits: dict) -> Path:
    for pid, (manifest, fids) in packs.items():
        d = root / "domain_packs" / pid
        d.mkdir(parents=True)
        body = {"manifest": manifest, "formulas": [{"formula_id": f} for f in fids]}
        # packs carry a BOM, as some real Store packs do
        (d / "pack.json").write_text("\ufeff" + json.dumps(body), encoding="utf-8")
    for kid, (serves, iids) in kits.items():
        d = root / "app" / "blocks" / kid
        d.mkdir(parents=True)
        (d / "manifest.yaml").write_text(f"kit: {kid}\nserves_verticals: {json.dumps(serves)}\n", encoding="utf-8")
        inv = "invariants:\n" + "".join(f"  - id: {i}\n    kind: band\n" for i in iids)
        (d / "invariants.yaml").write_text(inv, encoding="utf-8")
    return root


@pytest.mark.parametrize("ident, layer, label", [
    ("k1", "kernel", ""),          # in both: kernel wins
    ("s1", "store", ""),
    ("s2", "store", ""),
    ("new_x", "improvised", IMPROVISED_LABEL),
])
def test_resolve_layer_is_kernel_then_store_then_improvised(ident, layer, label):
    kernel = [{"id": "k1"}, {"id": "k2"}]
    store = [{"id": "k1"}, {"id": "s1"}, {"id": "s2"}]
    got = _fr().resolve_layer(ident, kernel, store)
    assert (got["layer"], got["label"]) == (layer, label)


GOOD = {"id": "imp_a", "label": IMPROVISED_LABEL, "source": "Book, ch. 2"}


@pytest.mark.parametrize("row, cands, expect", [
    (GOOD, [{"definition": "imp_a"}], []),
    ({**GOOD, "id": "imp_b"}, [{"definition": "imp_b"}], []),
    ({"id": "k1"}, [], ["k1: kernel covers this; use the kernel definition"]),
    ({"id": "s1", "label": IMPROVISED_LABEL, "source": "x"}, [{"definition": "s1"}],
     ["s1: the Store covers this; use the Store definition"]),
    ({**GOOD, "label": "made up"}, [{"definition": "imp_a"}],
     [f"imp_a: improvised without the label {IMPROVISED_LABEL!r}"]),
    ({**GOOD, "source": "  "}, [{"definition": "imp_a"}], ["imp_a: improvised without a cited source"]),
    (GOOD, [], ["imp_a: improvised without a new_domain_candidates row"]),
])
def test_writer_formula_errors(row, cands, expect):
    assert _fr().writer_formula_errors([row], [{"id": "k1"}], [{"id": "s1"}], cands) == expect


# --- R1: one test per G2 survivor (synthetic rows / trees only) -------------

IMP = {"label": IMPROVISED_LABEL, "source": "Ref, p. 1"}


@pytest.mark.parametrize("row, expect", [
    ({**IMP}, ["definition row without an id"]),
    ({**IMP, "id": "   "}, ["definition row without an id"]),
    ({**IMP, "id": "imp_n"}, []),
])
def test_s1_row_without_an_id_is_reported(row, expect):
    assert _fr().writer_formula_errors([row], [], [], [{"definition": "imp_n"}]) == expect


@pytest.mark.parametrize("prov, expect", [
    ({"reference": "Handbook, s. 4"}, []),
    ({"reference": "  "}, ["imp_p: improvised without a cited source"]),
])
def test_s2_provenance_reference_counts_as_the_source(prov, expect):
    row = {"id": "imp_p", "label": IMPROVISED_LABEL, "provenance": prov}
    assert _fr().writer_formula_errors([row], [], [], [{"definition": "imp_p"}]) == expect


@pytest.mark.parametrize("cands, expect", [
    ([{"id": "imp_c"}], []),
    ([{"id": "imp_other"}], ["imp_c: improvised without a new_domain_candidates row"]),
])
def test_s3_candidate_row_may_name_it_by_id(cands, expect):
    row = {"id": "imp_c", **IMP}
    assert _fr().writer_formula_errors([row], [], [], cands) == expect


@pytest.mark.parametrize("key, expect", [
    ("imp_k", []),
    ("k9", ["k9: kernel covers this; use the kernel definition"]),
])
def test_s4_row_may_carry_its_id_as_key(key, expect):
    row = {"key": key, **IMP}
    assert _fr().writer_formula_errors([row], [{"id": "k9"}], [], [{"definition": "imp_k"}]) == expect


# --- K-F7 split: one test per G2 survivor of 84bf10d (own file) -------------

def _write_defs(root: Path, lines):
    fr = _fr()
    path = root / fr.WRITER_DEFINITIONS_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    cands = root / fr.WRITER_CANDIDATES_REL
    cands.write_text(json.dumps({"definition": "imp_g"}) + "\n", encoding="utf-8")


GOOD_ROW = json.dumps({"id": "imp_g", "label": IMPROVISED_LABEL, "source": "Ref"})


@pytest.mark.parametrize("lines, expect", [
    ([GOOD_ROW, "{not json"], ["definition row without an id"]),
    ([GOOD_ROW], []),
])
def test_n5_an_unreadable_row_is_reported_not_skipped(tmp_path, lines, expect):
    _write_defs(tmp_path, lines)
    assert _fr().audit_product_formulas(tmp_path, [], []) == expect


@pytest.mark.parametrize("lines, expect", [
    ([GOOD_ROW, json.dumps(["imp_z"])], ["definition row without an id"]),
    ([GOOD_ROW, json.dumps(7)], ["definition row without an id"]),
    ([GOOD_ROW], []),
])
def test_n6_a_non_object_row_is_a_row_without_an_id(tmp_path, lines, expect):
    _write_defs(tmp_path, lines)
    assert _fr().audit_product_formulas(tmp_path, [], []) == expect


@pytest.mark.parametrize("write, expect", [
    (False, []),
    (True, ["definition row without an id"]),
])
def test_n10_a_tree_without_formula_files_is_clean_not_a_crash(tmp_path, write, expect):
    if write:
        _write_defs(tmp_path, ["{not json"])
    try:
        got = _fr().audit_product_formulas(tmp_path, [], [])
    except OSError as exc:
        raise AssertionError(f"missing formula files crashed the audit: {type(exc).__name__}") from None
    assert got == expect
    rec = json.loads((tmp_path / _fr().FORMULA_FINDINGS_REL).read_text(encoding="utf-8"))
    assert rec == {"errors": expect}


# --- #91 repair: G2 0059Z survivors N11, R5 (own file) ----------------------

@pytest.mark.parametrize("lines, expect", [
    ([GOOD_ROW, "", "   "], []),
    (["", GOOD_ROW, "{not json"], ["definition row without an id"]),
])
def test_n11_blank_lines_in_the_writer_jsonl_are_not_rows(tmp_path, lines, expect):
    _write_defs(tmp_path, lines)
    assert _fr().audit_product_formulas(tmp_path, [], []) == expect


@pytest.mark.parametrize("cands, expect", [
    ([{"definition": " imp_r "}], []),
    ([{"id": "\timp_r"}], []),
    ([{"definition": " imp_other "}], ["imp_r: improvised without a new_domain_candidates row"]),
])
def test_r5_a_padded_candidate_row_still_names_it(cands, expect):
    row = {"id": "imp_r", **IMP}
    assert _fr().writer_formula_errors([row], [], [], cands) == expect


# --- #91 replacement: G2 dead-terms R3, R4, R6 (own file) -------------------

@pytest.mark.parametrize("ident, want_id, layer, label", [
    (" a ", "a", "kernel", ""),
    ("\tb\n", "b", "store", ""),
    (" c ", "c", "improvised", IMPROVISED_LABEL),
])
def test_r3_a_padded_ident_resolves_to_its_layer(ident, want_id, layer, label):
    got = _fr().resolve_layer(ident, [{"id": "a"}], [{"id": "b"}])
    assert got == {"id": want_id, "layer": layer, "label": label}


@pytest.mark.parametrize("kernel, store, ident, layer", [
    ([{"id": " a"}], [], "a", "kernel"),
    ([{"id": "x"}], [{"id": "b\t"}], "b", "store"),
    ([{"id": " a "}], [{"id": " a "}], "a", "kernel"),
    ([{"id": "x"}], [{"id": "y"}], "a", "improvised"),
])
def test_r4_padded_index_ids_still_resolve(kernel, store, ident, layer):
    assert _fr().resolve_layer(ident, kernel, store)["layer"] == layer


@pytest.mark.parametrize("lines, expect", [
    ([GOOD_ROW], []),
    (["{not json"], ["definition row without an id"]),
])
def test_r6_findings_land_at_the_literal_path(tmp_path, lines, expect):
    _write_defs(tmp_path, lines)
    assert _fr().audit_product_formulas(tmp_path, [], []) == expect
    literal = tmp_path / ".factory" / "writer_formula_findings.json"
    assert literal.is_file(), "findings not written to .factory/writer_formula_findings.json"
    assert json.loads(literal.read_text(encoding="utf-8")) == {"errors": expect}
    assert _fr().FORMULA_FINDINGS_REL == ".factory/writer_formula_findings.json"


# --- X1 (G2 #94 0c8e475): a blank or missing id never matches a blank index id

@pytest.mark.parametrize("ident, kernel, store", [
    ("", [{"id": ""}], []),
    ("", [], [{"id": None}]),
    (None, [{"id": None}], []),
    (None, [], [{"id": "  "}]),
    ("", [{}], [{}]),
])
def test_x1_blank_ident_is_improvised_even_against_blank_index_ids(ident, kernel, store):
    out = _fr().resolve_layer(ident, kernel, store)
    assert out == {"id": "", "layer": "improvised", "label": IMPROVISED_LABEL}


@pytest.mark.parametrize("ident, kernel, store, layer", [
    ("k1", [{"id": ""}, {"id": "k1"}], [], "kernel"),
    ("s1", [{"id": None}], [{"id": ""}, {"id": "s1"}], "store"),
    ("", [{"id": "k1"}], [{"id": "s1"}], "improvised"),
])
def test_x1_blank_index_rows_do_not_hide_real_ids(ident, kernel, store, layer):
    assert _fr().resolve_layer(ident, kernel, store)["layer"] == layer
