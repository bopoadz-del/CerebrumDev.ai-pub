"""Knowledge items as Store objects: the schema, report-only (rule A1).

Design note SPEC_FACTORY_KNOWLEDGE_WIRING_GUARANTEE_15NOV, rule A1. A
product's figures live in the Store as one of two item kinds, beside the
existing pack sections:

* a **formula** (``formulas[]``): id, version, expression, a unit on every
  input and on the output, the ``quantity`` it measures, its ``basis``, a
  source that resolves to an ``authority_sources`` row, at least one worked
  example, and every constant the expression uses declared as an assumption
  (value, unit, source);
* a **data item** (``data_items[]``: tables, norms, rates, thresholds): id,
  version, quantity, basis, typed columns with a unit on every value column,
  rows, a source per row or for the item, and a tier.

This module only *validates* and *reports*. Nothing here refuses a build,
blocks a plan or changes a gate: :func:`store_schema_report` counts findings
per reason over every pack in a Store tree and returns them. Turning any
reason into a refusal is a later change the advisor reads first.

Every check reads the item's own declaration. No pack, vertical, item id or
value is named here.
"""

from __future__ import annotations

import ast
import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set

#: The four provenance tiers (rule F). An item's tier is one of these.
TIERS = ("customer_document", "reference", "benchmark", "assumption")
#: A source id an item may give to say "this is a declared assumption".
ASSUMPTION_SOURCE = "assumption"
#: Keys of an ``authority_sources`` row that make it a citation.
SOURCE_ROW_KEYS = ("kind", "reference", "tier")
#: A citation names its edition or date AND where in it (table, clause, page).
SOURCE_EDITION_KEYS = ("edition", "date", "year")
SOURCE_LOCATOR_KEYS = ("table", "clause", "page", "section", "locator")
MODE = "report_only"


def _text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _source_id(item: Mapping[str, Any]) -> str:
    return _text(item.get("authority_source") or item.get("source"))


def source_findings(source_id: str, authority_sources: Mapping[str, Any]) -> List[str]:
    """Reasons a source id does not resolve to a located citation."""
    if not source_id:
        return ["source_missing"]
    if source_id == ASSUMPTION_SOURCE:
        return []
    row = authority_sources.get(source_id)
    if not isinstance(row, Mapping) or not all(_text(row.get(k)) for k in SOURCE_ROW_KEYS):
        return ["source_unresolved"]
    has_edition = any(_text(row.get(k)) for k in SOURCE_EDITION_KEYS)
    has_locator = any(_text(row.get(k)) for k in SOURCE_LOCATOR_KEYS)
    if not (has_edition and has_locator):
        return ["source_unlocated"]
    return []


def _free_names(expression: str) -> Optional[Set[str]]:
    """Variable names the expression reads; ``None`` when it does not parse.

    A name in call position (``max(...)``) or as an attribute base is an
    operator the evaluator owns, not a quantity, so it is not counted.
    """
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError:
        return None
    skip: Set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            skip.add(id(node.func))
        elif isinstance(node, ast.Attribute):
            skip.add(id(node.value))
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and id(n) not in skip}


def _assumption_ok(rec: Any, authority_sources: Mapping[str, Any]) -> bool:
    if not isinstance(rec, Mapping):
        return False
    if not (_text(rec.get("name")) and _text(rec.get("unit"))) or rec.get("value") is None:
        return False
    return not source_findings(_source_id(rec), authority_sources)


def _example_ok(rec: Any) -> bool:
    return (
        isinstance(rec, Mapping)
        and isinstance(rec.get("inputs"), Mapping)
        and rec.get("expected") is not None
        and bool(_text(rec.get("unit")))
    )


def formula_findings(spec: Mapping[str, Any], authority_sources: Mapping[str, Any]) -> List[str]:
    """Sorted, de-duplicated reasons ``spec`` fails the formula schema."""
    out: Set[str] = set()
    for key, reason in (("formula_id", "id_missing"), ("version", "version_missing"),
                        ("quantity", "quantity_missing"), ("basis", "basis_missing")):
        if not spec.get(key):
            out.add(reason)
    inputs = [i for i in (spec.get("inputs") or []) if isinstance(i, Mapping)]
    if any(not _text(i.get("unit")) for i in inputs):
        out.add("input_unit_missing")
    output = spec.get("output") if isinstance(spec.get("output"), Mapping) else {}
    units = spec.get("units") if isinstance(spec.get("units"), Mapping) else {}
    if not (_text(output.get("unit")) or _text(units.get("output"))):
        out.add("output_unit_missing")
    out.update(source_findings(_source_id(spec), authority_sources))
    examples = spec.get("worked_examples") or []
    if not examples:
        out.add("worked_examples_missing")
    elif not all(_example_ok(e) for e in examples):
        out.add("worked_example_malformed")
    assumptions = spec.get("assumptions") or []
    if not all(_assumption_ok(a, authority_sources) for a in assumptions):
        out.add("assumption_malformed")
    expression = _text(spec.get("expression"))
    names = _free_names(expression) if expression else None
    if not expression:
        out.add("expression_missing")
    elif names is None:
        out.add("expression_unparseable")
    else:
        declared = {_text(i.get("name")) for i in inputs}
        declared |= {_text(a.get("name")) for a in assumptions if isinstance(a, Mapping)}
        if names - declared:
            out.add("expression_free_name")
    return sorted(out)


def data_item_findings(item: Mapping[str, Any], authority_sources: Mapping[str, Any]) -> List[str]:
    """Sorted, de-duplicated reasons ``item`` fails the data-item schema."""
    out: Set[str] = set()
    for key, reason in (("id", "id_missing"), ("version", "version_missing"),
                        ("quantity", "quantity_missing"), ("basis", "basis_missing")):
        if not item.get(key):
            out.add(reason)
    tier = _text(item.get("tier"))
    if not tier:
        out.add("tier_missing")
    elif tier not in TIERS:
        out.add("tier_unknown")
    columns = [c for c in (item.get("columns") or []) if isinstance(c, Mapping)]
    if not columns:
        out.add("columns_missing")
    item_unit = _text(item.get("unit"))
    values = [c for c in columns if _text(c.get("role") or "value") == "value"]
    if any(not (_text(c.get("unit")) or item_unit) for c in values):
        out.add("column_unit_missing")
    rows = item.get("rows") or []
    if not rows:
        out.add("rows_empty")
    names = {_text(c.get("name")) for c in columns}
    if any(not isinstance(r, Mapping) or not names.issubset(set(r)) for r in rows):
        out.add("row_malformed")
    item_source = _source_id(item)
    if item_source:
        out.update(source_findings(item_source, authority_sources))
    else:
        for row in rows:
            out.update(source_findings(_source_id(row) if isinstance(row, Mapping) else "",
                                       authority_sources))
        if not rows:
            out.add("source_missing")
    return sorted(out)


def pack_findings(pack: Mapping[str, Any]) -> List[Dict[str, str]]:
    """Every schema finding in one pack, as ``{kind, item_id, reason}`` rows."""
    raw_sources = pack.get("authority_sources")
    sources: Mapping[str, Any] = raw_sources if isinstance(raw_sources, Mapping) else {}
    rows: List[Dict[str, str]] = []
    for sid, row in sorted(sources.items()):
        if not isinstance(row, Mapping):
            rows.append({"kind": "pack", "item_id": _text(sid), "reason": "authority_source_not_a_row"})
    if "data_items" not in pack:
        rows.append({"kind": "pack", "item_id": "", "reason": "data_items_section_missing"})
    for spec in pack.get("formulas") or []:
        if isinstance(spec, Mapping):
            fid = _text(spec.get("formula_id"))
            rows += [{"kind": "formula", "item_id": fid, "reason": r}
                     for r in formula_findings(spec, sources)]
    for item in pack.get("data_items") or []:
        if isinstance(item, Mapping):
            iid = _text(item.get("id"))
            rows += [{"kind": "data_item", "item_id": iid, "reason": r}
                     for r in data_item_findings(item, sources)]
    return rows


def _items(pack: Mapping[str, Any], key: str) -> List[Mapping[str, Any]]:
    return [x for x in (pack.get(key) or []) if isinstance(x, Mapping)]


def summarize(packs: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Counts per ``kind.reason`` over ``packs``; ``mode`` is always report-only."""
    counts: Counter = Counter()
    formulas = data_items = formulas_failing = data_items_failing = 0
    for pack in packs:
        sources = pack.get("authority_sources")
        sources = sources if isinstance(sources, Mapping) else {}
        for spec in _items(pack, "formulas"):
            formulas += 1
            formulas_failing += bool(formula_findings(spec, sources))
        for item in _items(pack, "data_items"):
            data_items += 1
            data_items_failing += bool(data_item_findings(item, sources))
        for row in pack_findings(pack):
            counts[f"{row['kind']}.{row['reason']}"] += 1
    return {
        "mode": MODE,
        "packs": len(packs),
        "formulas": formulas,
        "formulas_failing": formulas_failing,
        "data_items": data_items,
        "data_items_failing": data_items_failing,
        "counts": dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))),
    }


def load_store_packs(store_root: Any) -> List[Mapping[str, Any]]:
    """Every readable ``domain_packs/*/pack.json`` under ``store_root``, sorted."""
    packs: List[Mapping[str, Any]] = []
    for path in sorted(Path(store_root).joinpath("domain_packs").glob("*/pack.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        if isinstance(data, Mapping):
            packs.append(data)
    return packs


def store_schema_report(store_root: Any) -> Dict[str, Any]:
    """The report over a Store tree. Report only: it never raises on a finding."""
    return summarize(load_store_packs(store_root))


def stocking_schema_record(store_root: Any) -> Dict[str, Any]:
    """The schema report as the CLONER records it at the stocking step (H-3a).

    Report only and never raises: a build with no Store root, or a Store
    tree that cannot be read, records why instead of crashing the CLONER.
    """
    if store_root is None:
        return {"mode": MODE, "recorded": False, "reason": "no_store_root"}
    try:
        report = store_schema_report(store_root)
    except (OSError, TypeError, ValueError) as exc:
        return {"mode": MODE, "recorded": False, "reason": "unreadable", "error": str(exc)}
    report["recorded"] = True
    return report


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Knowledge-item schema report (report only)")
    ap.add_argument("--store", required=True, help="Store checkout root (holds domain_packs/)")
    args = ap.parse_args(argv)
    print(json.dumps(store_schema_report(args.store), indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
