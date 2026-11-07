"""Program Kernel — a structured program engine (tracks, terms, weeks, sessions,
rubric grading, calendar, faculty prompts, export).

The content (tracks, themes, readings, cases, deliverables) lives in the
domain packs under ``app/factory/standards/domain_packs/executive_programs/``,
never in this package. This package is the engine: schema, calendar, rubric,
modes, prompts and export.
"""

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
]
