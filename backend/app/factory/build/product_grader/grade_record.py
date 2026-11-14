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
from typing import Any, Callable, Dict, List, Mapping, Optional

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


def normalise_verdicts(verdicts: Any) -> List[Dict[str, str]]:
    """FG-5: the one place a Layer-2 verdict is shaped before it is recorded.
    Question and verdict become strings; evidence that is not a string is
    recorded as none, so the verdict cannot pass on it and the record stays
    readable with that question open."""
    out: List[Dict[str, str]] = []
    for v in verdicts or []:
        if not isinstance(v, Mapping):
            # Not a verdict: kept, open, with nothing to pass on.
            v = {"question": v}
        ev = v.get("evidence")
        out.append({
            "question": str(v.get("question") or ""),
            "verdict": str(v.get("verdict") or ""),
            "evidence": ev if isinstance(ev, str) else "",
        })
    return out


def open_layer2(layer2: Mapping[str, Any]) -> List[Dict[str, str]]:
    """FG-5: THE derivation of what is open in Layer 2, read by the tier, the
    writer's work list, the reason classes, the record and the Store gate.
    Every recorded verdict that is not a PASS (with the grader's verdict and
    quoted evidence), then every recorded open question not already listed."""
    out = [v for v in normalise_verdicts(layer2.get("verdicts")) if not is_pass(v)]
    seen = {v["question"] for v in out}
    for q in layer2.get("open_fails") or []:
        if str(q) not in seen:
            seen.add(str(q))
            out.append({"question": str(q), "verdict": "", "evidence": ""})
    return out


def open_fails(layer2: Mapping[str, Any]) -> List[str]:
    """The questions of :func:`open_layer2`."""
    return [row["question"] for row in open_layer2(layer2)]


def layer2_task(row: Mapping[str, str]) -> str:
    """The writer's work item for one open Layer-2 question; an answered
    question carries the grader's verdict and its quoted evidence."""
    task = f"re-run writer on: {row['question']}"
    if row.get("verdict"):
        task += f" -- grader {row['verdict']}: \"{row.get('evidence') or ''}\""
    return task


def hardwiring_ok(hardwiring: Optional[Mapping[str, Any]]) -> bool:
    """FG-5: the one hardwiring rule, read by the record's certified flag and
    the Store gate alike. No section is no finding; a section that did not
    run or is not clean blocks."""
    return hardwiring is None or (hardwiring.get("ran") is True and hardwiring.get("clean") is True)


def layer1_ok(functions: Mapping[str, Any], ui: Mapping[str, Any]) -> bool:
    return functions.get("ok") is True and ui.get("ok") is True


def hardwiring_section(
    function_verdicts: Mapping[str, str],
    forbidden_hits: Mapping[str, Any],
    prompt_leaks: Mapping[str, Any],
    origin: Optional[Callable[[str], Any]] = None,
) -> Dict[str, Any]:
    """F-8e grade section. ``function_verdicts``: {capability_id: verdict} from
    the two-dataset check; ``forbidden_hits``: {relpath: [literals]} baked into
    the product source; ``prompt_leaks``: {relpath: [clauses]} of the supplied
    brief the writer was given and copied into that file (FG-4). A FAIL verdict (anything but the two-dataset OK / BOTH_ABSENT),
    a baked literal, or a prompt leak is a finding. ``clean`` == no findings.

    ``origin`` (FG-1) answers who wrote a file (factory_receipt.file_origin):
    each baked-literal and prompt-leak finding then carries its ``owner`` and ``owner_reason``.
    The bar is unchanged -- a Factory-owned finding still makes the section
    unclean; ownership only decides who the finding is billed to."""
    from app.factory.build.product_grader.two_dataset import BOTH_ABSENT, OK

    findings: List[Dict[str, Any]] = []
    for cid, verdict in sorted(function_verdicts.items()):
        if verdict not in (OK, BOTH_ABSENT):
            findings.append({"kind": "function", "capability": str(cid), "verdict": str(verdict)})
    for kind, rows in (("forbidden_literal", forbidden_hits), ("prompt_leak", prompt_leaks)):
        for path, lits in sorted(rows.items()):
            row: Dict[str, Any] = {"kind": kind, "file": str(path), "literals": [str(x) for x in lits]}
            if origin is not None:
                who = origin(str(path))
                row["owner"] = str(who.owner)
                row["owner_reason"] = str(who.reason)
            findings.append(row)
    return {"ran": True, "clean": not findings, "findings": findings}


def finding_is_writers(finding: Mapping[str, Any]) -> bool:
    """A hardwiring finding is billed to the writer unless its recorded owner
    is someone else (a finding with no owner field predates FG-1 and keeps
    its old billing)."""
    from app.factory.build.factory_receipt import WRITER

    return str(finding.get("owner") or WRITER) == WRITER


def _hardwiring_tasks(hardwiring: Optional[Mapping[str, Any]]) -> List[str]:
    if not hardwiring:
        return []
    out: List[str] = []
    for f in hardwiring.get("findings") or []:
        if not finding_is_writers(f):
            continue
        if f.get("kind") == "function":
            out.append(f"de-hardwire {f.get('capability')}: {f.get('verdict')}")
        elif f.get("kind") == "forbidden_literal":
            out.append(f"remove baked literals from {f.get('file')}: {f.get('literals')}")
        elif f.get("kind") == "prompt_leak":
            out.append(f"stop copying the brief into {f.get('file')}: {f.get('literals')}")
    return out


def build_grade(
    *, product_id: str, functions: Mapping[str, Any], ui: Mapping[str, Any], layer2: Mapping[str, Any],
    hardwiring: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    l1 = layer1_ok(functions, ui)
    l2 = dict(layer2, verdicts=normalise_verdicts(layer2.get("verdicts")))
    rows = open_layer2(l2)
    l2["open_fails"] = [row["question"] for row in rows]
    grade: Dict[str, Any] = {
        "schema": SCHEMA,
        "product_id": product_id,
        "layer1": {"ok": l1, "functions": dict(functions), "ui": dict(ui)},
        "layer2": l2,
        "next_tasks": [layer2_task(row) for row in rows] + _hardwiring_tasks(hardwiring),
        "certified": l1 and l2.get("ran") is True and bool(l2["verdicts"]) and not rows and hardwiring_ok(hardwiring),
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
    hardwiring = grade.get("hardwiring")
    if not hardwiring_ok(hardwiring):
        found = len(hardwiring.get("findings") or [])
        return f"product grade hardwiring scan not clean ({found} finding(s)): build not certified"
    return None
