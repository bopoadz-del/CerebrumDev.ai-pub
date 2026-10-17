"""Structured halt — every stop path in every module funnels here."""
from __future__ import annotations

from typing import Any, Dict, Optional


class Halt(Exception):
    """A run-stopping condition with evidence and the exact resume input."""

    def __init__(self, condition: str, evidence: Optional[Dict[str, Any]] = None, resume_input: str = ""):
        super().__init__(condition)
        self.condition = condition
        self.evidence = evidence or {}
        self.resume_input = resume_input

    def as_dict(self) -> Dict[str, Any]:
        return {"condition": self.condition, "evidence": self.evidence, "resume_input": self.resume_input}


def halt(condition: str, evidence: Optional[Dict[str, Any]] = None, resume_input: str = "") -> None:
    """Stop the run. Always raises; every halt becomes a structured report."""
    raise Halt(condition, evidence, resume_input)
