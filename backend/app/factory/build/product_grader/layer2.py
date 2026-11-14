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
import logging
import re
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: The ONLY inputs Layer 2 may see, in this order.
LAYER2_INPUTS = ("compiled_brief", "rendered_product", "layer1")

GRADER_SYSTEM_PROMPT = (
    "You are the GRADER of a generated software product, not its writer. "
    "You see only the compiled brief, the rendered product and the "
    "deterministic Layer-1 results. For each question answer JSON "
    '{"id": str, "verdict": "PASS"|"FAIL", "evidence": str} where id is the '
    "question's id copied exactly and evidence QUOTES the product. "
    "Return ONLY a JSON list."
)
EVIDENCE_MAX = 500

_FENCE_START = re.compile(r"^```[A-Za-z0-9_+-]*\s*\n?")
_FENCE_END = re.compile(r"\n?```\s*$")


def _strip_fences(text: str) -> str:
    """Strip a Markdown code fence around the grader's JSON answer."""
    text = (text or "").strip()
    text = _FENCE_START.sub("", text)
    text = _FENCE_END.sub("", text)
    return text.strip()


class ContextLeak(ValueError):
    """Layer 2 was offered something other than its three inputs."""


def layer2_messages(inputs: Mapping[str, Any], questions: Sequence[Mapping[str, str]]) -> List[Dict[str, str]]:
    """Exactly two messages: the grader system prompt, then one user message
    whose JSON body has exactly the three inputs plus the questions, each
    sent as ``{"id", "text"}`` so the answer is matched by id."""
    extra = sorted(k for k in inputs if k not in LAYER2_INPUTS)
    if extra:
        raise ContextLeak(f"layer 2 refuses non-grader inputs: {extra}")
    if not questions:
        raise ValueError("layer 2 needs at least one question")
    body: Dict[str, Any] = {k: inputs.get(k) for k in LAYER2_INPUTS}
    body["questions"] = [{"id": q["id"], "text": q["text"]} for q in questions]
    return [
        {"role": "system", "content": GRADER_SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(body, default=str, sort_keys=True)},
    ]


def layer2_questions(blueprint: Mapping[str, Any]) -> List[Dict[str, str]]:
    """One per declared capability (asking the requirements lines the writer
    was given, else its description), one per declared screen, and always
    the grounding question. Each is a record with a positional id."""
    out: List[Dict[str, str]] = []
    for c in blueprint.get("capabilities") or []:
        reqs = [str(r).strip() for r in (c.get("requirements") or []) if str(r).strip()]
        ask = str(c.get("description") or "")
        if reqs:
            ask = f"{ask} Requirements: " + " | ".join(reqs)
        out.append({
            "capability": str(c.get("id") or ""),
            "text": f"Does the product deliver capability '{c.get('id')}' as the brief asked: {ask}?",
        })
    for m in blueprint.get("ui_modules") or []:
        out.append({"capability": "", "text": f"Does screen '{m}' render the capability it is declared for?"})
    out.append({"capability": "", "text": "Is every number the product shows grounded in a kernel computation or a cited source?"})
    for n, q in enumerate(out, 1):
        q["id"] = f"q{n}"
    return out


def _collect(
    raw_list: Sequence[Any], questions: Sequence[Mapping[str, str]]
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Match answers to questions by id only. The recorded question is our
    text, never the grader's echo; an unknown or repeated id is not an
    answer. Returns (verdicts, the texts of the questions left open)."""
    by_id = {q["id"]: q for q in questions}
    clean: List[Dict[str, Any]] = []
    seen: set = set()
    for v in raw_list:
        if not isinstance(v, Mapping):
            continue
        qid = str(v.get("id") or "").strip()
        if qid not in by_id or qid in seen:
            continue
        seen.add(qid)
        ev = v.get("evidence")
        clean.append({
            "question": by_id[qid]["text"],
            "verdict": v.get("verdict"),
            "evidence": ev[:EVIDENCE_MAX] if isinstance(ev, str) else ev,
        })
    return clean, [q["text"] for q in questions if q["id"] not in seen]


def run_layer2(
    inputs: Mapping[str, Any],
    blueprint: Mapping[str, Any],
    complete: Callable[[List[Dict[str, str]]], str],
    render: Optional[Callable[[Sequence[str]], str]] = None,
) -> Dict[str, Any]:
    """``complete`` is one fresh llm call. One call per capability question
    (with that capability's files rendered first and whole when ``render``
    is given), then one call for the screen and grounding questions. A call
    that does not answer opens only its own questions; every question the
    grader did not answer by id is an open FAIL. ``ran`` is False only when
    every call failed."""
    questions = layer2_questions(blueprint)
    groups = [[q] for q in questions if q["capability"]]
    tail = [q for q in questions if not q["capability"]]
    if tail:
        groups.append(tail)
    for group in groups:
        layer2_messages(inputs, group)  # ContextLeak propagates before any call
    verdicts: List[Dict[str, Any]] = []
    open_fails: List[str] = []
    answered_calls = 0
    for group in groups:
        cid = group[0]["capability"]
        view = dict(inputs)
        if render is not None and cid:
            view["rendered_product"] = render([cid.replace("-", "_")])
        try:
            parsed = json.loads(_strip_fences(complete(layer2_messages(view, group))))
            if not isinstance(parsed, list):
                raise ValueError("not a JSON list")
        except Exception as exc:  # noqa: BLE001 -- recorded as an open FAIL per question
            logger.warning("layer 2 call did not answer: %s", type(exc).__name__)
            open_fails += [f"layer 2 did not answer: {type(exc).__name__}: {q['text']}" for q in group]
            continue
        answered_calls += 1
        got, missing = _collect(parsed, group)
        verdicts += got
        open_fails += missing
    return {"ran": answered_calls > 0, "verdicts": verdicts, "open_fails": open_fails}
