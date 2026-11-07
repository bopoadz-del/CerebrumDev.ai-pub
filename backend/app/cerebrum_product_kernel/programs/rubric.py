"""Rubric scoring for the Program Kernel.

Thresholds come from ``rubric.bands`` — never from literals. Scores are the
five criterion scores, each 0..20, so a memo totals 0..100.
"""

from __future__ import annotations

from typing import Literal, Sequence

from .schema import Rubric, Weights


def total(scores: Sequence[int | None]) -> int | None:
    """Sum of the five criterion scores, or ``None`` when any is missing.

    Every present score must be 0..20 — a score outside that is a data error,
    raised loudly rather than folded into a grade.
    """
    if any(s is None for s in scores):
        return None
    for s in scores:
        if s < 0 or s > 20:
            raise ValueError(f"criterion score {s} out of range 0..20")
    return sum(int(s) for s in scores)


def band(total_score: int, rubric: Rubric) -> Literal["A", "B", "C", "rewrite"]:
    """The letter band for a 0..100 total, from ``rubric.bands`` thresholds."""
    b = rubric.bands
    if total_score >= b["A"]:
        return "A"
    if total_score >= b["B"]:
        return "B"
    if total_score >= b["C"]:
        return "C"
    return "rewrite"


def weighted_average(
    memo_totals: Sequence[int | None],
    defense_results: Sequence[str],
    capstone_status: str | None,
    weights: Weights,
) -> float | None:
    """The 0..100 program average from memo/defense/capstone, by weight.

    Memos average the graded weekly totals; each defense is 100 on ``PASS``
    and 0 on ``FAIL``; the capstone is 100 on ``ACCEPT``, 0 on ``REVISE``,
    and ``None`` (excluded) before a verdict. Any ungraded input excludes its
    component; if nothing is graded the result is ``None``.
    """
    graded_memos = [t for t in memo_totals if t is not None]
    memo_avg = sum(graded_memos) / len(graded_memos) if graded_memos else None

    defense_scores = [100 if d == "PASS" else 0 for d in defense_results]
    defense_avg = (
        sum(defense_scores) / len(defense_scores) if defense_scores else None
    )

    capstone = {
        "ACCEPT": 100,
        "REVISE": 0,
    }.get(capstone_status)

    weighted: list[tuple[float, int]] = []
    if memo_avg is not None:
        weighted.append((memo_avg, weights.memos))
    if defense_avg is not None:
        weighted.append((defense_avg, weights.defenses))
    if capstone is not None:
        weighted.append((float(capstone), weights.capstone))

    total_weight = sum(w for _, w in weighted)
    if total_weight == 0:
        return None
    return sum(score * w for score, w in weighted) / total_weight


def graduated(
    average: float | None,
    defense_results: Sequence[str],
    capstone_status: str | None,
    rubric: Rubric,
) -> bool:
    """Passed when the average reaches the B band, every defense PASSes and
    the capstone is ACCEPTed."""
    if average is None or average < rubric.bands["B"]:
        return False
    if not defense_results or any(d != "PASS" for d in defense_results):
        return False
    return capstone_status == "ACCEPT"
