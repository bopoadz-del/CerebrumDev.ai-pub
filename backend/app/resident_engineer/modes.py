"""Resident Mode autonomy levels — L3–L5 are draft-only."""

from __future__ import annotations

from enum import Enum, auto
from typing import Any, Dict, Optional, Union

from app.resident_engineer.injection_guard import strip_instruction_patterns


class AutonomyLevel(str, Enum):
    """Resident Mode autonomy levels. The wire value is the member name
    (``AutonomyLevel.L3 == "L3"``), so code names a level by member and the
    JSON it emits is unchanged."""

    @staticmethod
    def _generate_next_value_(name: str, start: int, count: int, last_values: list) -> str:
        return name

    L1 = auto()  # observe
    L2 = auto()  # allowlisted heal
    L3 = auto()  # draft change request only
    L4 = auto()
    L5 = auto()


DRAFT_ONLY_LEVELS = frozenset({AutonomyLevel.L3, AutonomyLevel.L4, AutonomyLevel.L5})


def draft_change_request(
    *,
    level: Union[AutonomyLevel, str],
    kind: str,
    summary: str,
    product_id: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Surface L3–L5 work as a draft change request — never execute."""
    try:
        level = AutonomyLevel(level)
    except ValueError:
        raise ValueError(f"unknown autonomy level: {level}") from None
    return {
        "schema_version": "1.0.0",
        "kind": kind,
        "level": level.value,
        "product_id": product_id,
        "summary": strip_instruction_patterns(summary or ""),
        "details": details or {},
        "execution": "allowed" if level is AutonomyLevel.L2 else "draft_only",
        "status": "draft" if level in DRAFT_ONLY_LEVELS else "observable",
        "note": (
            "L3–L5 produce draft change requests only; intake/execution is Milestone 3+"
            if level in DRAFT_ONLY_LEVELS
            else "Resident Mode L1/L2 surface"
        ),
    }
