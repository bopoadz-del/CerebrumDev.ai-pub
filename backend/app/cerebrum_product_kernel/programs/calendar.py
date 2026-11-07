"""Program calendar: the session template and the date arithmetic.

The session template and the Thursday-start week rule are the file's own;
nothing here is track content.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

#: (offset-days-from-week-start, kind, minutes) — the file's SESSIONS.
SESSION_TEMPLATE = (
    (0, "framework", 90),
    (1, "case_prep", 60),
    (2, "case_class", 120),
    (4, "lab", 90),
    (5, "lab", 90),
    (6, "exam", 60),
)

_KIND_NAME = {
    "framework": "Framework",
    "case_prep": "Case prep",
    "case_class": "Case class",
    "exam": "Exam and grade",
}


@dataclass(frozen=True)
class Session:
    week: int
    kind: str
    offset: int
    minutes: int
    date: date
    name: str


def week_start(start: date, n: int) -> date:
    """The Monday (program week day 0) of week ``n`` (1-indexed)."""
    return start + timedelta(days=7 * (n - 1))


def sessions(start: date, length_weeks: int) -> list[Session]:
    """Every session of the program, week by week, with concrete dates."""
    out: list[Session] = []
    for week in range(1, length_weeks + 1):
        ws = week_start(start, week)
        lab_index = 0
        for offset, kind, minutes in SESSION_TEMPLATE:
            if kind == "lab":
                lab_index += 1
                name = f"Lab {lab_index}"
            else:
                name = _KIND_NAME[kind]
            out.append(
                Session(
                    week=week,
                    kind=kind,
                    offset=offset,
                    minutes=minutes,
                    date=ws + timedelta(days=offset),
                    name=name,
                )
            )
    return out


def current_week(start: date, today: date, length_weeks: int) -> int:
    """``0`` before the program starts, ``length_weeks + 1`` after it ends."""
    diff = (today - start).days
    if diff < 0:
        return 0
    n = diff // 7 + 1
    return n if n <= length_weeks else length_weeks + 1
