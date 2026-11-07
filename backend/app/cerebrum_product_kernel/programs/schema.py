"""track.v1 schema for the Program Kernel.

The schema is closed (``extra="forbid"``): a field the engine does not know
about must fail loudly, not be silently carried. Every validator raises
``ValueError`` — never ``assert``, which vanishes under ``python -O``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class TrackError(ValueError):
    """A track document is invalid; the message carries the dotted field path."""


def _forbid() -> ConfigDict:
    return ConfigDict(extra="forbid")


class Reading(BaseModel):
    model_config = _forbid()

    title: str
    author: str
    chapters: str | None = None


class Case(BaseModel):
    model_config = _forbid()

    title: str
    decision_question: str
    public_sources: list[str] = Field(default_factory=list)


class Deliverable(BaseModel):
    model_config = _forbid()

    title: str
    brief: str
    pages: int = Field(ge=1)


class Week(BaseModel):
    model_config = _forbid()

    number: int
    theme: str
    readings: list[Reading]
    case: Case
    deliverable: Deliverable


class Term(BaseModel):
    model_config = _forbid()

    number: int
    name: str
    outcome: str
    defense_minutes: int = 45
    weeks: list[Week]


class Criterion(BaseModel):
    model_config = _forbid()

    name: str
    full_marks: str


class Rubric(BaseModel):
    model_config = _forbid()

    points_each: Literal[20] = 20
    bands: dict[Literal["A", "B", "C", "rewrite_below"], int]
    criteria: list[Criterion] = Field(min_length=5, max_length=5)


class Weights(BaseModel):
    model_config = _forbid()

    memos: int
    defenses: int
    capstone: int

    @model_validator(mode="after")
    def _sum_100(self) -> "Weights":
        if self.memos + self.defenses + self.capstone != 100:
            raise ValueError("weights must sum to 100")
        return self


class Track(BaseModel):
    model_config = _forbid()

    schema_version: Literal["track.v1"]
    id: str = Field(pattern=r"^[a-z0-9-]+$")
    name: str
    audience: str
    length_weeks: int = Field(ge=4)
    hours_per_week: float
    rubric: Rubric
    weights: Weights
    terms: list[Term]

    @model_validator(mode="after")
    def _weeks_match(self) -> "Track":
        n = sum(len(t.weeks) for t in self.terms)
        if n != self.length_weeks:
            raise ValueError(f"{n} weeks listed, length_weeks={self.length_weeks}")
        return self


def load_track(path: Path) -> Track:
    """Load one track.v1 document.

    YAML parse and pydantic validation errors are re-raised as
    :class:`TrackError` carrying the dotted field path, so a bad document is
    attributable to a field, never a traceback.
    """
    raw = Path(path).read_text(encoding="utf-8")
    try:
        data: Any = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise TrackError(f"invalid YAML: {exc}") from exc
    try:
        return Track.model_validate(data)
    except ValidationError as exc:
        errs = exc.errors()
        first = errs[0] if errs else {}
        loc = ".".join(str(p) for p in first.get("loc", ())) or "track"
        msg = first.get("msg", "invalid")
        raise TrackError(f"{loc}: {msg}") from exc
