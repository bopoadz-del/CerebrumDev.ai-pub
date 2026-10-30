"""F-8c-2 -- Layer 2: the DeepSeek grader in a SEPARATE context.

A fresh call (no history) with its own grader system prompt, fed ONLY the
compiled brief, the rendered product and the Layer-1 results -- never the
writer's prompt, transcript or reasoning. Questions come from the
blueprint. This module records what the grader said; it does not decide.
The pass/fail decision is re-derived from the recorded verdicts by the
Builds gate (``grade_record.certification_refusal``).
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Mapping, Sequence

#: The ONLY inputs Layer 2 may see, in this order.
LAYER2_INPUTS = ("compiled_brief", "rendered_product", "layer1")

GRADER_SYSTEM_PROMPT = (
    "You are the GRADER of a generated software product, not its writer. "
    "You see only the compiled brief, the rendered product and the "
    "deterministic Layer-1 results. For each question, answer JSON "
    '{"question": str, "verdict": "PASS"|"FAIL", "evidence": str} where '
    "evidence QUOTES the product. Return ONLY a JSON list."
)
EVIDENCE_MAX = 500


class ContextLeak(ValueError):
    """Layer 2 was offered something other than its three inputs."""


def layer2_messages(inputs: Mapping[str, Any], questions: Sequence[str]) -> List[Dict[str, str]]:
    """Exactly two messages: the grader system prompt, then one user message
    whose JSON body has exactly the three inputs plus the questions."""
    extra = sorted(k for k in inputs if k not in LAYER2_INPUTS)
    if extra:
        raise ContextLeak(f"layer 2 refuses non-grader inputs: {extra}")
    if not questions:
        raise ValueError("layer 2 needs at least one question")
    body: Dict[str, Any] = {k: inputs.get(k) for k in LAYER2_INPUTS}
    body["questions"] = [str(q) for q in questions]
    return [
        {"role": "system", "content": GRADER_SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(body, default=str, sort_keys=True)},
    ]


def layer2_questions(blueprint: Mapping[str, Any]) -> List[str]:
    """One per declared capability, one per declared screen, and always the
    grounding question."""
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
    """``complete`` is one fresh llm call. No answer = ran False with an open
    FAIL; every question the grader did not answer is an open FAIL."""
    questions = layer2_questions(blueprint)
    messages = layer2_messages(inputs, questions)  # ContextLeak propagates
    try:
        verdicts = json.loads(complete(messages))
    except Exception as exc:  # noqa: BLE001 -- no verdict is an open FAIL, recorded
        return {"ran": False, "verdicts": [], "open_fails": [f"layer 2 did not answer: {type(exc).__name__}"]}
    if not isinstance(verdicts, list):
        return {"ran": False, "verdicts": [], "open_fails": ["layer 2 did not answer: not a JSON list"]}
    clean = [
        {"question": str(v.get("question") or ""), "verdict": v.get("verdict"),
         "evidence": v.get("evidence")[:EVIDENCE_MAX] if isinstance(v.get("evidence"), str) else v.get("evidence")}
        for v in verdicts if isinstance(v, Mapping)
    ]
    answered = {v["question"] for v in clean}
    return {"ran": True, "verdicts": clean, "open_fails": [q for q in questions if q not in answered]}
