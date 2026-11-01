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

# The one writer of grade.json is F-8c-0's (#79); no copy lives here.
from app.factory.build.product_grader.grade_writer import write_grade_json  # noqa: F401

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


def hardwiring_section(
    function_verdicts: Mapping[str, str],
    forbidden_hits: Mapping[str, Any],
    prompt_leaks: Any,
) -> Dict[str, Any]:
    """F-8e grade section. ``function_verdicts``: {capability_id: verdict} from
    the two-dataset check; ``forbidden_hits``: {relpath: [literals]} baked into
    the product source; ``prompt_leaks``: literals shared by the writer prompt
    and the source. A FAIL verdict (anything but the two-dataset OK / BOTH_ABSENT),
    a baked literal, or a prompt leak is a finding. ``clean`` == no findings."""
    from app.factory.build.product_grader.two_dataset import BOTH_ABSENT, OK

    findings: List[Dict[str, Any]] = []
    for cid, verdict in sorted(function_verdicts.items()):
        if verdict not in (OK, BOTH_ABSENT):
            findings.append({"kind": "function", "capability": str(cid), "verdict": str(verdict)})
    for path, lits in sorted(forbidden_hits.items()):
        findings.append({"kind": "forbidden_literal", "file": str(path), "literals": [str(x) for x in lits]})
    if prompt_leaks:
        findings.append({"kind": "prompt_leak", "literals": [str(x) for x in prompt_leaks]})
    return {"ran": True, "clean": not findings, "findings": findings}


def _hardwiring_tasks(hardwiring: Optional[Mapping[str, Any]]) -> List[str]:
    if not hardwiring:
        return []
    out: List[str] = []
    for f in hardwiring.get("findings") or []:
        if f.get("kind") == "function":
            out.append(f"de-hardwire {f.get('capability')}: {f.get('verdict')}")
        elif f.get("kind") == "forbidden_literal":
            out.append(f"remove baked literals from {f.get('file')}: {f.get('literals')}")
        elif f.get("kind") == "prompt_leak":
            out.append(f"stop leaking the prompt into the source: {f.get('literals')}")
    return out


def build_grade(
    *, product_id: str, functions: Mapping[str, Any], ui: Mapping[str, Any], layer2: Mapping[str, Any],
    hardwiring: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    l1 = layer1_ok(functions, ui)
    fails = open_fails(layer2)
    hw_ok = hardwiring is None or (hardwiring.get("ran") is True and hardwiring.get("clean") is True)
    grade: Dict[str, Any] = {
        "schema": SCHEMA,
        "product_id": product_id,
        "layer1": {"ok": l1, "functions": dict(functions), "ui": dict(ui)},
        "layer2": dict(layer2, open_fails=fails),
        "next_tasks": [f"re-run writer on: {q}" for q in fails] + _hardwiring_tasks(hardwiring),
        "certified": l1 and layer2.get("ran") is True and bool(layer2.get("verdicts")) and not fails and hw_ok,
    }
    if hardwiring is not None:
        grade["hardwiring"] = dict(hardwiring)
    return grade


def _schema_errors(grade: Any) -> List[str]:
    """Every way ``grade`` is not a product_grade.v1 record (empty == valid)."""
    if not isinstance(grade, Mapping):
        return ["record is not a mapping"]
    errs: List[str] = []
    want = {"schema", "product_id", "layer1", "layer2", "next_tasks", "certified"}
    missing = want - set(grade)
    unknown = set(grade) - want - {"hardwiring"}  # hardwiring is an optional F-8e section
    if missing or unknown:
        errs.append(f"fields {sorted(missing | unknown)} missing or unknown")
    hw = grade.get("hardwiring")
    if hw is not None and (
        not isinstance(hw, Mapping)
        or not isinstance(hw.get("ran"), bool)
        or not isinstance(hw.get("clean"), bool)
        or not isinstance(hw.get("findings"), list)
    ):
        errs.append("hardwiring is not {ran: bool, clean: bool, findings: list}")
    if grade.get("schema") != SCHEMA:
        errs.append("schema is not product_grade.v1")
    if not isinstance(grade.get("product_id"), str) or not grade["product_id"].strip():
        errs.append("product_id is not a non-blank string")
    if not isinstance(grade.get("certified"), bool):
        errs.append("certified is not a bool")
    if not isinstance(grade.get("next_tasks"), list) or not all(isinstance(t, str) for t in grade["next_tasks"]):
        errs.append("next_tasks is not a list of strings")
    l1, l2 = grade.get("layer1"), grade.get("layer2")
    if not isinstance(l1, Mapping):
        errs.append("layer1 is not a mapping")
    else:
        if not isinstance(l1.get("ok"), bool):
            errs.append("layer1.ok is not a bool")
        for part in ("functions", "ui"):
            if not isinstance(l1.get(part), Mapping) or not isinstance(l1[part].get("ok"), bool):
                errs.append(f"layer1.{part} is not a mapping with a bool ok")
    if not isinstance(l2, Mapping):
        errs.append("layer2 is not a mapping")
    else:
        if not isinstance(l2.get("ran"), bool):
            errs.append("layer2.ran is not a bool")
        vs = l2.get("verdicts")
        if not isinstance(vs, list) or not all(
            isinstance(v, Mapping) and all(isinstance(v.get(k), str) for k in ("question", "verdict", "evidence")) for v in vs
        ):
            errs.append("layer2.verdicts is not a list of {question, verdict, evidence} strings")
        of = l2.get("open_fails")
        if not isinstance(of, list) or not all(isinstance(q, str) for q in of):
            errs.append("layer2.open_fails is not a list of strings")
    return errs


def _load(root: Path | str) -> Mapping[str, Any]:
    grade = json.loads((Path(root) / GRADE_JSON).read_text("utf-8"))
    errs = _schema_errors(grade)
    if errs:
        raise ValueError("not a product_grade.v1 record: " + "; ".join(errs))
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
