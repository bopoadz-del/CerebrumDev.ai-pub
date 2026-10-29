"""F-8c-1 -- grade.json and the Builds certification decision.

``grade.json`` carries both layers (ORDERS_1730Z):

* Layer 1 -- deterministic F-8a function grade and F-8b UI grade. This
  layer ALONE can certify.
* Layer 2 -- the separate-context DeepSeek grader (F-8c-2). It can only
  BLOCK: an open FAIL, a verdict without quoted evidence, or a Layer 2 that
  did not run all refuse handover.

``certification_refusal`` is what the Builds gate (n3_store_gate) consumes.
It re-derives the decision from the recorded layers; a hand-set
``certified: true`` is never trusted.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

GRADE_JSON = "grade.json"
SCHEMA = "product_grade.v1"
PASS = "PASS"


def is_pass(verdict: Any) -> bool:
    """A Layer-2 verdict is a PASS only when it says exactly ``PASS`` and
    quotes non-blank evidence. Anything else -- FAIL, ``pass``, ``Pass``,
    blank or whitespace evidence, a non-mapping -- is an open FAIL."""
    if not isinstance(verdict, Mapping):
        return False
    evidence = verdict.get("evidence")
    return verdict.get("verdict") == PASS and isinstance(evidence, str) and bool(evidence.strip())


def open_fails(layer2: Mapping[str, Any]) -> List[str]:
    """Every recorded open FAIL plus every recorded verdict that is not a PASS."""
    out = [str(q) for q in layer2.get("open_fails") or []]
    for v in layer2.get("verdicts") or []:
        q = str(v.get("question") if isinstance(v, Mapping) else v)
        if not is_pass(v) and q not in out:
            out.append(q)
    return out


def layer1_ok(functions: Mapping[str, Any], ui: Mapping[str, Any]) -> bool:
    return functions.get("ok") is True and ui.get("ok") is True


def build_grade(
    *, product_id: str, functions: Mapping[str, Any], ui: Mapping[str, Any], layer2: Mapping[str, Any]
) -> Dict[str, Any]:
    l1 = layer1_ok(functions, ui)
    fails = open_fails(layer2)
    return {
        "schema": SCHEMA,
        "product_id": product_id,
        "layer1": {"ok": l1, "functions": dict(functions), "ui": dict(ui)},
        "layer2": dict(layer2, open_fails=fails),
        "next_tasks": [f"re-run writer on: {q}" for q in fails],
        "certified": l1 and layer2.get("ran") is True and bool(layer2.get("verdicts")) and not fails,
    }


def write_grade_json(root: Path | str, grade: Mapping[str, Any]) -> Path:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / GRADE_JSON
    path.write_text(json.dumps(grade, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _load(root: Path | str) -> Mapping[str, Any]:
    grade = json.loads((Path(root) / GRADE_JSON).read_text("utf-8"))
    if not isinstance(grade, Mapping) or grade.get("schema") != SCHEMA:
        raise ValueError("not a product_grade.v1 record")
    l1, l2 = grade["layer1"], grade["layer2"]
    if not all(isinstance(x, Mapping) for x in (l1, l2, l1["functions"], l1["ui"])):
        raise ValueError("layers are not mappings")
    if not isinstance(l2.get("verdicts", []), list):
        raise ValueError("verdicts is not a list")
    return grade


def certification_refusal(root: Path | str) -> Optional[str]:
    """Why the Builds gate must refuse this build, or None."""
    if not (Path(root) / GRADE_JSON).is_file():
        return "no product grade (grade.json missing): build not certified"
    try:
        grade = _load(root)
    except (OSError, ValueError, KeyError, TypeError):
        return "product grade unreadable: build not certified"
    l1, l2 = grade["layer1"], grade["layer2"]
    if not layer1_ok(l1["functions"], l1["ui"]):
        return "product grade Layer 1 RED: build not certified"
    if l2.get("ran") is not True:
        return "product grade Layer 2 did not run: handover blocked"
    if not l2.get("verdicts"):
        return "product grade Layer 2 returned no verdicts: handover blocked"
    fails = open_fails(l2)
    if fails:
        return f"product grade has {len(fails)} open Layer-2 FAIL(s): handover blocked"
    return None
