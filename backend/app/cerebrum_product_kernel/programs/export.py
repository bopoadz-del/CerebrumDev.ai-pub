"""Markdown export and JSON backup/restore for the Program Kernel.

The layout matches the reference file's ``markdown()``: one ``## Week n``
section per week, the memo verbatim, the grade block only when a memo is
graded, and the defense/jury lines only when they exist.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

from pydantic import BaseModel, Field

from .calendar import week_start
from .rubric import band, total
from .schema import Track

_DAYN = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
_MONTH_SHORT = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]
_MONTH_LONG = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


class RestoreError(ValueError):
    """A backup blob is not a backup this app wrote."""


class WeekState(BaseModel):
    memo: str = ""
    scores: list[str] = Field(default_factory=lambda: ["", "", "", "", ""])
    verdicts: list[str] = Field(default_factory=lambda: ["", "", "", "", ""])
    defense: str = ""
    jury: str = ""


class State(BaseModel):
    track: str
    start: str
    data: dict[str, dict[int, WeekState]]


def _fmt(d: date) -> str:
    return f"{_DAYN[d.weekday()]} {d.day} {_MONTH_SHORT[d.month - 1]} {d.year}"


def _fmt_long(d: date) -> str:
    return f"{_DAYN[d.weekday()]} {d.day} {_MONTH_LONG[d.month - 1]} {d.year}"


def _band_text(b: str | None) -> str:
    return "Rewrite" if b == "rewrite" else (b or "")


def markdown(track: Track, state: State) -> str:
    """The full export, byte-identical in layout to the file's ``markdown()``."""
    start = date.fromisoformat(state.start)
    week_states = state.data.get(track.id, {})
    lines = [
        f"# {track.name}",
        "",
        f"Started {_fmt_long(start)} \u00b7 exported {_fmt_long(date.today())}",
        "",
    ]
    for n in range(1, track.length_weeks + 1):
        ws = week_states.get(n, WeekState())
        term_no = (n - 1) // 4 + 1
        week = track.terms[term_no - 1].weeks[(n - 1) % 4]
        lines.extend(
            [
                f"## Week {n} \u2014 {week.theme}",
                "",
                (
                    f"Deliverable: {week.deliverable.title} \u00b7 due "
                    f"{_fmt(week_start(start, n) + timedelta(days=5))}"
                ),
                "",
                ws.memo if ws.memo else "_(no memo)_",
                "",
            ]
        )
        scores = [int(s) if s != "" else None for s in ws.scores]
        t = total(scores) if len(scores) == 5 else None
        if t is not None:
            lines.append(f"Grade: {t} \u00b7 {_band_text(band(t, track.rubric))}")
            for i, criterion in enumerate(track.rubric.criteria):
                verdict = ws.verdicts[i] if i < len(ws.verdicts) else ""
                score = ws.scores[i] if i < len(ws.scores) else ""
                lines.append(f"- {criterion.name}: {score}/20 \u2014 {verdict}")
            lines.append("")
        if ws.defense:
            lines.append(f"Term {term_no} defense: {ws.defense}")
            lines.append("")
        if ws.jury:
            lines.append(f"Capstone jury: {ws.jury}")
            lines.append("")
    return "\n".join(lines)


def backup(state: State) -> str:
    """The JSON blob ``restore`` accepts back."""
    return json.dumps(state.model_dump(), indent=1)


def restore(text: str) -> State:
    """Parse a backup blob. Anything without a top-level ``data`` key is not
    a backup this app wrote — refused, never silently accepted."""
    try:
        obj: Any = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise RestoreError("not a JSON backup") from exc
    if not isinstance(obj, dict) or "data" not in obj:
        raise RestoreError("backup has no top-level 'data' key")
    return State.model_validate(obj)
