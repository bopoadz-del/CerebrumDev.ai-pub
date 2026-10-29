"""F-8c -- the product grade record and the certification rule.

``grade.json`` + ``GRADE.md`` carry both layers (ORDERS_1730Z):

* Layer 1 -- deterministic: the F-8a function grade and the F-8b UI grade
  (their ``to_dict()`` shapes). This layer ALONE decides certification.
* Layer 2 -- a DeepSeek grader in a SEPARATE context: a fresh call with its
  own grader system prompt, fed ONLY the compiled brief, the rendered
  product and the Layer-1 results. Never the writer's prompt, transcript or
  reasoning. It returns structured verdicts with quoted evidence. A Layer-2
  PASS never certifies a product Layer 1 failed; a Layer-2 FAIL is a named
  next task and blocks handover.

``certification_refusal`` is what the Builds gate consumes: None only when
a grade exists, Layer 1 is green and no Layer-2 FAIL is open.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

GRADE_JSON = "grade.json"
GRADE_MD = "GRADE.md"
SCHEMA = "product_grade.v1"

#: The ONLY inputs Layer 2 may see. Anything else offered is refused.
LAYER2_INPUTS = ("compiled_brief", "rendered_product", "layer1")
#: Writer-context keys whose presence in Layer-2 input is a context leak.
WRITER_CONTEXT_KEYS = ("writer_prompt", "writer_transcript", "writer_reasoning", "transcript", "messages")

GRADER_SYSTEM_PROMPT = (
    "You are the GRADER of a generated software product, not its writer. "
    "You see only the compiled brief, the rendered product and the "
    "deterministic Layer-1 results. For each question, answer JSON "
    '{"question": str, "verdict": "PASS"|"FAIL", "evidence": str} where '
    "evidence QUOTES the product. Return ONLY a JSON list."
)


class ContextLeak(ValueError):
    """Layer 2 was offered writer context -- it would grade its own work."""


def layer2_messages(inputs: Mapping[str, Any], questions: Sequence[str]) -> List[Dict[str, str]]:
    leaked = [k for k in inputs if k in WRITER_CONTEXT_KEYS or k not in LAYER2_INPUTS]
    if leaked:
        raise ContextLeak(f"layer 2 refuses non-grader inputs: {sorted(leaked)}")
    body = {k: inputs.get(k) for k in LAYER2_INPUTS}
    body["questions"] = list(questions)
    return [
        {"role": "system", "content": GRADER_SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(body, default=str)},
    ]


def layer2_questions(blueprint: Mapping[str, Any]) -> List[str]:
    """Brief-specific questions, one per declared capability and screen."""
    qs = [
        f"Does the product deliver capability '{c.get('id')}' as the brief asked: {c.get('description')}?"
        for c in blueprint.get("capabilities") or []
    ]
    qs += [f"Does screen '{m}' render the capability it is declared for?" for m in blueprint.get("ui_modules") or []]
    qs.append("Is every number the product shows grounded in a kernel computation or a cited source?")
    return qs


def run_layer2(
    inputs: Mapping[str, Any],
    blueprint: Mapping[str, Any],
    complete: Callable[[List[Dict[str, str]]], str],
) -> Dict[str, Any]:
    """``complete`` is the single llm_client call (a fresh call -- no history)."""
    questions = layer2_questions(blueprint)
    try:
        raw = complete(layer2_messages(inputs, questions))
        verdicts = json.loads(raw)
        if not isinstance(verdicts, list):
            raise ValueError("not a list")
    except ContextLeak:
        raise
    except Exception as exc:  # noqa: BLE001 -- no verdict is an open FAIL, never a pass
        return {"ran": False, "verdicts": [], "open_fails": [f"layer 2 did not answer: {type(exc).__name__}"]}
    clean = []
    for v in verdicts:
        if not isinstance(v, Mapping):
            continue
        verdict = "PASS" if str(v.get("verdict")).upper() == "PASS" and str(v.get("evidence") or "").strip() else "FAIL"
        clean.append({"question": str(v.get("question") or ""), "verdict": verdict, "evidence": str(v.get("evidence") or "")[:500]})
    answered = {v["question"] for v in clean}
    open_fails = [v["question"] for v in clean if v["verdict"] == "FAIL"]
    open_fails += [q for q in questions if q not in answered]
    return {"ran": True, "verdicts": clean, "open_fails": open_fails}


def build_grade(
    *, product_id: str, functions: Mapping[str, Any], ui: Mapping[str, Any], layer2: Mapping[str, Any]
) -> Dict[str, Any]:
    layer1_ok = bool(functions.get("ok")) and bool(ui.get("ok"))
    open_fails = list(layer2.get("open_fails") or [])
    return {
        "schema": SCHEMA,
        "product_id": product_id,
        "layer1": {"ok": layer1_ok, "functions": dict(functions), "ui": dict(ui)},
        "layer2": dict(layer2),
        "next_tasks": [f"re-run writer on: {q}" for q in open_fails],
        # Layer 1 alone certifies; Layer 2 can only block.
        "certified": layer1_ok and bool(layer2.get("ran")) and not open_fails,
    }


def render_md(grade: Mapping[str, Any]) -> str:
    l1, l2 = grade["layer1"], grade["layer2"]
    fn, ui = l1["functions"], l1["ui"]
    lines = [
        f"# GRADE -- {grade['product_id']}",
        "",
        f"**Certified:** {'YES' if grade['certified'] else 'NO'} (Layer 1 decides; Layer 2 can only block)",
        "",
        f"## Layer 1 (deterministic): {'GREEN' if l1['ok'] else 'RED'}",
        f"- Functions graded: {fn.get('graded', 0)} (min {fn.get('min_functions', '?')})",
    ]
    for f in fn.get("functions") or []:
        lines.append(
            f"  - `{f['capability_id']}`: {'PASS' if f.get('ok') else 'FAIL'}; mutants killed "
            f"{', '.join(f.get('mutants_killed') or []) or 'none'}; survived {', '.join(f.get('mutants_survived') or []) or 'none'}"
        )
    lines.append(f"- UI steps passed: {sum(1 for s in ui.get('steps') or [] if s.get('passed'))}/{len(ui.get('steps') or [])}")
    for name in ui.get("failing") or []:
        lines.append(f"  - FAIL: {name}")
    lines += ["", f"## Layer 2 (separate grader context): {'ran' if l2.get('ran') else 'did not run'}"]
    for v in l2.get("verdicts") or []:
        lines.append(f"- {v['verdict']}: {v['question']} -- evidence: \"{v['evidence']}\"")
    if grade.get("next_tasks"):
        lines += ["", "## Next tasks"] + [f"- {t}" for t in grade["next_tasks"]]
    return "\n".join(lines) + "\n"


def write_grade(root: Path | str, grade: Mapping[str, Any]) -> None:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    (root / GRADE_JSON).write_text(json.dumps(grade, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (root / GRADE_MD).write_text(render_md(grade), encoding="utf-8")


def certification_refusal(root: Path | str) -> Optional[str]:
    """Why the Builds gate must refuse this build, or None.

    Recomputed from the record's own layers -- a hand-set ``certified: true``
    over a red Layer 1 is still refused."""
    path = Path(root) / GRADE_JSON
    if not path.is_file():
        return "no product grade (grade.json missing): build not certified"
    try:
        grade = json.loads(path.read_text("utf-8"))
        l1 = grade["layer1"]
        fn_ok, ui_ok = bool(l1["functions"]["ok"]), bool(l1["ui"]["ok"])
        open_fails = list(grade.get("layer2", {}).get("open_fails") or [])
        ran = bool(grade.get("layer2", {}).get("ran"))
        # A recorded PASS is only a PASS with quoted evidence.
        open_fails += [
            str(v.get("question"))
            for v in grade.get("layer2", {}).get("verdicts") or []
            if not (str(v.get("verdict")).upper() == "PASS" and str(v.get("evidence") or "").strip())
            and str(v.get("question")) not in open_fails
        ]
    except Exception:  # noqa: BLE001
        return "product grade unreadable: build not certified"
    if not (fn_ok and ui_ok):
        return "product grade Layer 1 RED: build not certified"
    if not ran:
        return "product grade Layer 2 did not run: handover blocked"
    if open_fails:
        return f"product grade has {len(open_fails)} open Layer-2 FAIL(s): handover blocked"
    return None
