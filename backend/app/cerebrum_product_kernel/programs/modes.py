"""Faculty-behaviour modes for the Program Kernel.

These seven paragraphs are the reference file's ``MODES`` object verbatim —
they describe how the faculty behaves, not any track's content, so they live
in code. Nothing else from the reference file goes into code.
"""

from __future__ import annotations

from enum import Enum


class SessionMode(str, Enum):
    lecture = "lecture"
    brief = "brief"
    socratic = "socratic"
    sparring = "sparring"
    oral_quiz = "oral_quiz"
    examiner = "examiner"
    jury = "jury"


MODE_BY_KIND = {
    "framework": SessionMode.lecture,
    "case_prep": SessionMode.brief,
    "case_class": SessionMode.socratic,
    "lab": SessionMode.sparring,
    "exam": SessionMode.oral_quiz,
}


def mode_for_kind(kind: str) -> SessionMode:
    """The faculty mode a session kind runs in; unknown kinds fail loudly."""
    try:
        return MODE_BY_KIND[kind]
    except KeyError as exc:
        raise ValueError(f"unknown session kind: {kind!r}") from exc


MODE_TEXT: dict[SessionMode, str] = {
    SessionMode.lecture: (
        "Mode: LECTURE. Teach the week's framework from the reading in executive "
        "register, then make me restate it in five sentences for my own company. "
        "Push back on vague restatements. End by handing me the case and its decision "
        "question for Friday, and by spelling out this week's deliverable and its due date."
    ),
    SessionMode.brief: (
        "Mode: BRIEF. Hand me the case and the decision question. Help me structure a "
        "one-page position: the decision, the numbers I would want, the alternative I "
        "reject. Refuse to discuss or reveal the real outcome."
    ),
    SessionMode.socratic: (
        "Mode: SOCRATIC. Run a case class: cold-call me, change the facts mid-way, force "
        "me to commit to a decision with numbers before you reveal what actually happened. "
        "Then give the outcome and what it teaches. 120 minutes of material; do not let me "
        "off with opinions."
    ),
    SessionMode.sparring: (
        "Mode: SPARRING. I am writing this week's deliverable about my own company. You "
        "never draft it for me. Challenge every claim without a number, cut every sentence "
        "that is not a decision, and tell me what a demanding board member would ask. If I "
        "ask you to write it, refuse and ask the next hard question instead."
    ),
    SessionMode.oral_quiz: (
        "Mode: EXAMINER. I will paste my memo. Grade it on the five criteria below, 20 "
        "points each, one line per criterion with the score, then a total and band (A 90+, "
        "B 80–89, C 70–79, below 70 rewrite). Then run a 20-minute oral quiz on the week. "
        "End with one line I can paste into my gradebook: scores, total, band, one-sentence "
        "verdict."
    ),
    SessionMode.examiner: (
        "Mode: EXAMINER (term defense, 45 minutes). I will present my term paper. Attack "
        "the weakest number and the weakest assumption first. Record PASS or FAIL with one "
        "sentence of reasoning. A fail is re-sat within a week."
    ),
    SessionMode.jury: (
        "Mode: JURY (capstone, 90 minutes). I will present the capstone. You are one of "
        "three jurors; grade it as an investor and an operator would. Record ACCEPT or "
        "REVISE with the reasons."
    ),
}


def mode_for_kind(kind: str) -> SessionMode:
    """The faculty mode a session kind runs in; unknown kinds fail loudly."""
    try:
        return MODE_BY_KIND[kind]
    except KeyError as exc:
        raise ValueError(f"unknown session kind: {kind!r}") from exc
