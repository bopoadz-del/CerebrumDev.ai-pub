"""Rule A1: the knowledge-item schema, report-only.

Every id, unit, quantity and number here comes from a seeded generator inside
the test, never from a real product, vertical or Store item. Each test runs
under two independent seeds and must give the same verdict (nothing keys on
a name).
"""

from __future__ import annotations

import copy
import json
import random

import pytest

from app.factory.build import knowledge_schema as ks

SEEDS = [7, 4242]


def _name(rng: random.Random, prefix: str) -> str:
    return prefix + "_" + "".join(rng.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(8))


def _world(seed: int) -> dict:
    """A pack whose one formula and one data item pass every check."""
    rng = random.Random(seed)
    src = _name(rng, "src")
    a, b, k = _name(rng, "in"), _name(rng, "in"), _name(rng, "k")
    unit_a, unit_b, unit_out = _name(rng, "u"), _name(rng, "u"), _name(rng, "u")
    key_col, val_col = _name(rng, "col"), _name(rng, "col")
    sources = {src: {"kind": "standard", "reference": _name(rng, "pub"), "tier": "reference",
                     "edition": str(rng.randint(1990, 2030)), "table": str(rng.randint(1, 99))}}
    formula = {
        "formula_id": _name(rng, "f"), "version": "1.0.0",
        "expression": f"{a} * {b} + max({k}, 0)",
        "inputs": [{"name": a, "unit": unit_a}, {"name": b, "unit": unit_b}],
        "output": {"name": _name(rng, "out"), "unit": unit_out},
        "quantity": _name(rng, "q"), "basis": {"per": _name(rng, "per")},
        "authority_source": src,
        "worked_examples": [{"inputs": {a: rng.randint(1, 9), b: rng.randint(1, 9)},
                             "expected": rng.randint(1, 99), "unit": unit_out}],
        "assumptions": [{"name": k, "value": rng.randint(1, 9), "unit": unit_out, "source": src}],
    }
    item = {
        "id": _name(rng, "d"), "version": "1.0.0", "quantity": _name(rng, "q"),
        "basis": {"per": _name(rng, "per")}, "tier": rng.choice(ks.TIERS),
        "columns": [{"name": key_col, "role": "key"}, {"name": val_col, "unit": _name(rng, "u")}],
        "rows": [{key_col: _name(rng, "r"), val_col: rng.randint(1, 99), "source": src}],
    }
    return {"authority_sources": sources, "formulas": [formula], "data_items": [item]}


def _reasons(pack: dict) -> list:
    return sorted(f"{r['kind']}.{r['reason']}" for r in ks.pack_findings(pack))


@pytest.mark.parametrize("seed", SEEDS)
def test_valid_pack_has_no_findings(seed):
    assert _reasons(_world(seed)) == []


def _edit_formula(field, value):
    def edit(pack):
        f = pack["formulas"][0]
        if value is _DROP:
            f.pop(field, None)
        else:
            f[field] = value(f, pack) if callable(value) else value
    return edit


def _edit_item(field, value):
    def edit(pack):
        d = pack["data_items"][0]
        if value is _DROP:
            d.pop(field, None)
        else:
            d[field] = value(d, pack) if callable(value) else value
    return edit


_DROP = object()


def _first_source(pack):
    return next(iter(pack["authority_sources"]))


def _strip_unit_from_first_input(f, _pack):
    return [{"name": f["inputs"][0]["name"]}] + f["inputs"][1:]


def _drop_source_locator(pack):
    row = pack["authority_sources"][_first_source(pack)]
    row.pop("table")


def _drop_source_edition(pack):
    row = pack["authority_sources"][_first_source(pack)]
    row.pop("edition")


def _source_row_to_string(pack):
    pack["authority_sources"][_first_source(pack)] = "a bare string"


def _drop_row_source_and_item_source(pack):
    pack["data_items"][0]["rows"][0].pop("source")


def _row_missing_column(pack):
    d = pack["data_items"][0]
    d["rows"][0].pop(d["columns"][1]["name"])


def _value_column_no_unit(pack):
    pack["data_items"][0]["columns"][1].pop("unit")


FORMULA_CASES = [
    ("id", _edit_formula("formula_id", _DROP), ["formula.id_missing"]),
    ("version", _edit_formula("version", _DROP), ["formula.version_missing"]),
    ("quantity", _edit_formula("quantity", _DROP), ["formula.quantity_missing"]),
    ("basis", _edit_formula("basis", _DROP), ["formula.basis_missing"]),
    ("input_unit", _edit_formula("inputs", _strip_unit_from_first_input), ["formula.input_unit_missing"]),
    ("output_unit", _edit_formula("output", {"name": "x"}), ["formula.output_unit_missing"]),
    ("source_missing", _edit_formula("authority_source", _DROP), ["formula.source_missing"]),
    ("source_unresolved", _edit_formula("authority_source", "nowhere"), ["formula.source_unresolved"]),
    ("examples_missing", _edit_formula("worked_examples", []), ["formula.worked_examples_missing"]),
    ("example_bad", _edit_formula("worked_examples", [{"inputs": {}, "expected": 1}]),
     ["formula.worked_example_malformed"]),
    ("assumption_bad", _edit_formula("assumptions", lambda f, p: [dict(f["assumptions"][0], unit="")]),
     ["formula.assumption_malformed"]),
    ("assumption_not_a_row", _edit_formula("assumptions", lambda f, p: f["assumptions"] + ["a bare string"]),
     ["formula.assumption_malformed"]),
    ("expression_missing",_edit_formula("expression", ""), ["formula.expression_missing"]),
    ("expression_bad", _edit_formula("expression", "1 +"), ["formula.expression_unparseable"]),
    ("free_name", _edit_formula("expression", lambda f, p: f["expression"] + " + undeclared_constant"),
     ["formula.expression_free_name"]),
]

ITEM_CASES = [
    ("id", _edit_item("id", _DROP), ["data_item.id_missing"]),
    ("version", _edit_item("version", _DROP), ["data_item.version_missing"]),
    ("quantity", _edit_item("quantity", _DROP), ["data_item.quantity_missing"]),
    ("basis", _edit_item("basis", _DROP), ["data_item.basis_missing"]),
    ("tier_missing", _edit_item("tier", _DROP), ["data_item.tier_missing"]),
    ("tier_unknown", _edit_item("tier", "controlled"), ["data_item.tier_unknown"]),
    ("column_unit", _value_column_no_unit, ["data_item.column_unit_missing"]),
    ("row_malformed", _row_missing_column, ["data_item.row_malformed"]),
    ("row_source", _drop_row_source_and_item_source, ["data_item.source_missing"]),
]

PACK_CASES = [
    ("locator", _drop_source_locator, ["data_item.source_unlocated", "formula.assumption_malformed",
                                        "formula.source_unlocated"]),
    ("edition", _drop_source_edition, ["data_item.source_unlocated", "formula.assumption_malformed",
                                        "formula.source_unlocated"]),
    ("bare_string", _source_row_to_string, ["data_item.source_unresolved", "formula.assumption_malformed",
                                             "formula.source_unresolved", "pack.authority_source_not_a_row"]),
    ("no_section", lambda p: p.pop("data_items"), ["pack.data_items_section_missing"]),
]


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("case", FORMULA_CASES + ITEM_CASES + PACK_CASES, ids=lambda c: c[0])
def test_each_defect_gives_exactly_its_reason(seed, case):
    _label, edit, expected = case
    pack = _world(seed)
    edit(pack)
    assert _reasons(pack) == expected


@pytest.mark.parametrize("seed", SEEDS)
def test_verdicts_independent_of_ids(seed):
    """The whole case table gives the same verdicts under another seed."""
    other = SEEDS[1 - SEEDS.index(seed)]
    for _label, edit, _expected in FORMULA_CASES + ITEM_CASES + PACK_CASES:
        mine, theirs = _world(seed), _world(other)
        edit(mine)
        edit(theirs)
        assert _reasons(mine) == _reasons(theirs)


@pytest.mark.parametrize("seed", SEEDS)
def test_item_level_source_covers_rows(seed):
    pack = _world(seed)
    item = pack["data_items"][0]
    item["source"] = _first_source(pack)
    item["rows"][0].pop("source")
    assert _reasons(pack) == []


@pytest.mark.parametrize("seed", SEEDS)
def test_empty_rows_without_source(seed):
    pack = _world(seed)
    pack["data_items"][0]["rows"] = []
    assert _reasons(pack) == ["data_item.rows_empty", "data_item.source_missing"]


@pytest.mark.parametrize("seed", SEEDS)
def test_columns_missing(seed):
    pack = _world(seed)
    pack["data_items"][0]["columns"] = []
    assert _reasons(pack) == ["data_item.columns_missing"]


@pytest.mark.parametrize("seed", SEEDS)
def test_assumption_source_and_units_block(seed):
    """An explicit ``assumption`` source is allowed; a ``units.output`` counts as the output unit."""
    pack = _world(seed)
    f = pack["formulas"][0]
    f["authority_source"] = ks.ASSUMPTION_SOURCE
    f["units"] = {"output": f["output"].pop("unit")}
    item = pack["data_items"][0]
    item["unit"] = item["columns"][1].pop("unit")
    assert _reasons(pack) == []


@pytest.mark.parametrize("seed", SEEDS)
def test_attribute_base_is_not_a_free_name(seed):
    pack = _world(seed)
    f = pack["formulas"][0]
    f["expression"] = f["expression"] + " + math.pi"
    assert _reasons(pack) == []


@pytest.mark.parametrize("seed", SEEDS)
def test_summary_counts_per_reason_and_stays_report_only(seed, tmp_path):
    good, bad = _world(seed), _world(seed + 1)
    bad["formulas"][0].pop("quantity")
    bad["formulas"].append(copy.deepcopy(bad["formulas"][0]))
    bad["data_items"][0].pop("tier")
    for i, pack in enumerate((good, bad)):
        d = tmp_path / "domain_packs" / f"p{i}"
        d.mkdir(parents=True)
        (d / "pack.json").write_text(json.dumps(pack), encoding="utf-8")
    broken = tmp_path / "domain_packs" / "p9"
    broken.mkdir()
    (broken / "pack.json").write_text("{not json", encoding="utf-8")
    report = ks.store_schema_report(tmp_path)
    assert report == {
        "mode": "report_only", "packs": 2, "formulas": 3, "formulas_failing": 2,
        "data_items": 2, "data_items_failing": 1,
        "counts": {"formula.quantity_missing": 2, "data_item.tier_missing": 1},
    }


@pytest.mark.parametrize("seed", SEEDS)
def test_cli_prints_report_and_exits_zero(seed, tmp_path, capsys):
    pack = _world(seed)
    pack["formulas"][0].pop("basis")
    d = tmp_path / "domain_packs" / "p"
    d.mkdir(parents=True)
    (d / "pack.json").write_text(json.dumps(pack), encoding="utf-8")
    assert ks.main(["--store", str(tmp_path)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["counts"] == {"formula.basis_missing": 1}


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("n_packs", [1, 3])
def test_stocking_record_reports_the_store_it_was_given(seed, n_packs, tmp_path):
    """H-3a: the CLONER's record is the schema report of ITS Store tree."""
    for i in range(n_packs):
        d = tmp_path / "domain_packs" / f"p{i}"
        d.mkdir(parents=True)
        (d / "pack.json").write_text(json.dumps(_world(seed + i)), encoding="utf-8")
    record = ks.stocking_schema_record(tmp_path)
    assert record["recorded"] is True
    assert record["mode"] == ks.MODE
    assert (record["packs"], record["formulas"], record["data_items"]) == (n_packs,) * 3
    assert record["formulas_failing"] == 0


@pytest.mark.parametrize(
    "root, reason", [(None, "no_store_root"), (12345, "unreadable")]
)
def test_stocking_record_without_a_readable_store_says_why(root, reason):
    """No Store root, or one that is not a path, is recorded; the CLONER never crashes."""
    record = ks.stocking_schema_record(root)
    assert record["recorded"] is False
    assert record["reason"] == reason
    assert record["mode"] == ks.MODE
