"""Faculty prompts for the Program Kernel, byte-identical to the reference.

The rules-of-the-room paragraph and the mode text are faculty behaviour and
live in code; every track word (name, theme, reading, case, deliverable,
rubric criterion) comes from the :class:`Track` model, never a literal.
"""

from __future__ import annotations

from .calendar import Session
from .modes import MODE_TEXT, SessionMode, mode_for_kind
from .schema import Track

_RULES = (
    "You are faculty, case instructor and examiner for an executive program "
    "run on the case method. Everything applies to MY company, not a textbook "
    "one. Executive level, no basics unless asked. Agreement is not teaching: "
    "push back hard."
)


def _term_of(week_no: int) -> int:
    return (week_no + 3) // 4


def session_prompt(track: Track, week_no: int, session: Session) -> str:
    """The faculty prompt for one session, matching the file's ``prompt``."""
    term_no = _term_of(week_no)
    term = track.terms[term_no - 1]
    week = term.weeks[(week_no - 1) % 4]
    pages = week.deliverable.pages
    pages_plural = "s" if pages > 1 else ""
    reading_line = "; ".join(r.title for r in week.readings)

    lines = [
        f"FACULTY SESSION \u2014 {track.name}",
        (
            f"Week {week_no} of {track.length_weeks} \u00b7 Term {term_no}: "
            f"{term.name} \u00b7 Session: {session.name} ({session.minutes} min)"
        ),
        "",
        _RULES,
        "",
        "THIS WEEK",
        f"Theme: {week.theme}",
        f"Reading: {reading_line}",
        f"Case: {week.case.title}",
        f"Decision question: {week.case.decision_question}",
        (
            f"Deliverable: {week.deliverable.title} "
            f"({pages} page{pages_plural}) \u2014 {week.deliverable.brief}"
        ),
        "",
        MODE_TEXT[mode_for_kind(session.kind)],
        "",
        "RUBRIC (20 points each)",
    ]
    lines.extend(f"- {c.name}: {c.full_marks}" for c in track.rubric.criteria)
    lines.append("")
    lines.append("Start now with the first item of this session.")
    return "\n".join(lines)


def defense_prompt(track: Track, week_no: int) -> str:
    """The term-defense prompt, matching the file's ``defensePrompt``."""
    term_no = _term_of(week_no)
    term = track.terms[term_no - 1]
    return "\n".join(
        [
            f"FACULTY SESSION \u2014 {track.name}",
            f"Term {term_no} defense: {term.name}",
            "",
            f"Term outcome: {term.outcome}",
            "",
            MODE_TEXT[SessionMode.examiner],
        ]
    )


def jury_prompt(track: Track) -> str:
    """The capstone jury prompt, matching the file's ``juryPrompt``."""
    return "\n".join(
        [
            f"FACULTY SESSION \u2014 {track.name}",
            "Capstone jury",
            "",
            MODE_TEXT[SessionMode.jury],
        ]
    )
