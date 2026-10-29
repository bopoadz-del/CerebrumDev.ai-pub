"""F-8c-0 -- build-end step: write grade.json + GRADE.md.

Input is what the graders return: ``FunctionGrade.to_dict()`` (F-8a),
``UiGrade.to_dict()`` (F-8b) and the Layer-2 record (F-8c-2). Output is the
``product_grade.v1`` record the Builds gate (F-8c-1, #75) reads, at
``<root>/grade.json``, plus its human render ``<root>/GRADE.md``.

This step only WRITES; it never certifies. ``certified`` is recorded for
humans and recomputed by the gate, which trusts nothing hand-set. A grader
result missing or not shaped like a grade is written as a RED layer, never
dropped, so a build that skipped a grader still has a grade -- a failing one.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

SCHEMA = "product_grade.v1"
GRADE_JSON = "grade.json"
GRADE_MD = "GRADE.md"
PASS = "PASS"


def _layer1_part(result: Optional[Mapping[str, Any]], name: str) -> Dict[str, Any]:
    if not isinstance(result, Mapping):
        return {"ok": False, "failing": [f"{name} grader did not run"]}
    part = dict(result)
    part["ok"] = result.get("ok") is True
    return part


def _layer2_part(layer2: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    if not isinstance(layer2, Mapping):
        return {"ran": False, "verdicts": [], "open_fails": ["layer 2 did not run"]}
    verdicts = []
    for raw in layer2.get("verdicts") or []:
        if not isinstance(raw, Mapping):
            continue
        v = dict(raw)
        if not isinstance(v.get("evidence"), str):
            # Not evidence: recorded as none, so the verdict cannot pass on it.
            v["evidence"] = ""
        verdicts.append(v)
    fails = [str(q) for q in layer2.get("open_fails") or []]
    for v in verdicts:
        ev = v.get("evidence")
        ok = v.get("verdict") == PASS and bool(ev.strip())
        q = str(v.get("question") or "")
        if not ok and q not in fails:
            fails.append(q)
    return {"ran": layer2.get("ran") is True, "verdicts": verdicts, "open_fails": fails}


def build_record(
    *, product_id: str, functions: Optional[Mapping[str, Any]], ui: Optional[Mapping[str, Any]],
    layer2: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    if not str(product_id or "").strip():
        raise ValueError("product_id is required")
    fn, uip, l2 = _layer1_part(functions, "function"), _layer1_part(ui, "ui"), _layer2_part(layer2)
    l1_ok = fn["ok"] and uip["ok"]
    return {
        "schema": SCHEMA,
        "product_id": str(product_id),
        "layer1": {"ok": l1_ok, "functions": fn, "ui": uip},
        "layer2": l2,
        "next_tasks": [f"re-run writer on: {q}" for q in l2["open_fails"]],
        "certified": l1_ok and l2["ran"] and bool(l2["verdicts"]) and not l2["open_fails"],
    }


def _names(xs: Any) -> str:
    return ", ".join(str(x) for x in xs or []) or "none"


def render_md(grade: Mapping[str, Any]) -> str:
    l1, l2 = grade["layer1"], grade["layer2"]
    fn, ui = l1["functions"], l1["ui"]
    steps = ui.get("steps") or []
    lines: List[str] = [
        f"# GRADE -- {grade['product_id']}", "",
        f"**Certified:** {'YES' if grade['certified'] is True else 'NO'} (Layer 1 decides; Layer 2 can only block)", "",
        f"## Layer 1 (deterministic): {'GREEN' if l1['ok'] is True else 'RED'}",
        f"- Functions graded: {fn.get('graded', 0)} (min {fn.get('min_functions', '?')})",
    ]
    for f in fn.get("functions") or []:
        lines.append(f"  - `{f.get('capability_id')}`: {'PASS' if f.get('ok') is True else 'FAIL'}; "
                     f"mutants killed {_names(f.get('mutants_killed'))}; survived {_names(f.get('mutants_survived'))}")
    lines += [f"  - FAIL: {n}" for n in fn.get("failing") or []]
    lines.append(f"- UI steps passed: {sum(1 for s in steps if s.get('passed') is True)}/{len(steps)}")
    lines += [f"  - FAIL: {n}" for n in ui.get("failing") or []]
    lines += ["", f"## Layer 2 (separate grader context): {'ran' if l2['ran'] else 'did not run'}"]
    lines += [f"- {v.get('verdict')}: {v.get('question')} -- evidence: \"{v.get('evidence') or ''}\"" for v in l2["verdicts"]]
    lines += [f"- OPEN: {q}" for q in l2["open_fails"]]
    if grade["next_tasks"]:
        lines += ["", "## Next tasks"] + [f"- {t}" for t in grade["next_tasks"]]
    return "\n".join(lines) + "\n"


def write_build_grade(
    root: Path | str, *, product_id: str, functions: Optional[Mapping[str, Any]],
    ui: Optional[Mapping[str, Any]], layer2: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    """The build-end step. Writes both files (json last-but-atomic) and returns the record."""
    grade = build_record(product_id=product_id, functions=functions, ui=ui, layer2=layer2)
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    (root / GRADE_MD).write_text(render_md(grade), encoding="utf-8")
    write_grade_json(root, grade)
    return grade


def write_grade_json(root: Path | str, grade: Mapping[str, Any]) -> Path:
    """Write ``<root>/grade.json`` atomically (tmp + replace). The one writer
    of the file: the #75 gate and its tests import this, never a copy."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / GRADE_JSON
    tmp = root / (GRADE_JSON + ".tmp")
    tmp.write_text(json.dumps(grade, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path
