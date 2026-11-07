"""Program Kernel — a structured program engine (tracks, terms, weeks, sessions,
rubric grading, calendar, faculty prompts, export).

The content (tracks, themes, readings, cases, deliverables) lives in the
domain packs under ``app/factory/standards/domain_packs/executive_programs/``,
never in this package. This package is the engine: schema, calendar, rubric,
modes, prompts and export.
"""

from .calendar import Session, current_week, sessions, week_start
from .modes import MODE_BY_KIND, MODE_TEXT, SessionMode, mode_for_kind
from .prompts import defense_prompt, jury_prompt, session_prompt
from .rubric import band, graduated, total, weighted_average
from .schema import (
    Case,
    Criterion,
    Deliverable,
    Reading,
    Rubric,
    Term,
    Track,
    TrackError,
    Week,
    Weights,
    load_track,
)

__all__ = [
    "Reading",
    "Case",
    "Deliverable",
    "Week",
    "Term",
    "Criterion",
    "Rubric",
    "Weights",
    "Track",
    "TrackError",
    "load_track",
    "Session",
    "SessionMode",
    "MODE_BY_KIND",
    "MODE_TEXT",
    "mode_for_kind",
    "week_start",
    "sessions",
    "current_week",
    "total",
    "band",
    "weighted_average",
    "graduated",
    "session_prompt",
    "defense_prompt",
    "jury_prompt",
]
