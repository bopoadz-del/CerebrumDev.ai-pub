"""runner._phase_minutes_s: the failure-time per-phase snapshot."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.factory.build.authority import BuildRole
from app.factory.build.ledger import BuildLedger, EventKind
from app.factory.build.runner import RoleRunner


@pytest.mark.parametrize("gap_s", [120.0, 180.0])
def test_phase_minutes_counts_an_inflight_phase(tmp_path: Path, gap_s: float):
    """A PHASE_STARTED with no terminal still counts to the ledger's last
    timestamp; the drop_rule mutant skips the last-ts scan and drops it."""
    clock = {"t": 1_000_000.0}

    def ledger_clock() -> str:
        return datetime.fromtimestamp(clock["t"], tz=timezone.utc).isoformat()

    ledger = BuildLedger(tmp_path / "l.jsonl", clock=ledger_clock)
    ledger.start_run(product_id="p", inputs_hash="h")
    clock["t"] += 100.0
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER)
    clock["t"] += gap_s
    ledger.append(EventKind.NOTE, detail="wall snapshot")

    result = RoleRunner._phase_minutes_s(SimpleNamespace(ledger=ledger))
    assert result["WRITER"] == round(gap_s / 60.0, 2)


@pytest.mark.parametrize(
    "wall, want_class",
    [(1800.0, "small"), (2400.0, "new_platform")],
)
def test_note_plan_budget_appends_the_class(tmp_path: Path, wall: float, want_class: str):
    """_note_plan_budget writes the plan class: an empty class normalizes to
    small (<=1800s) or new_platform (>1800s). constant_return writes nothing;
    drop_rule skips the normalization and writes the raw empty class."""
    from app.factory.build.runner import BuildBudget

    ledger = BuildLedger(tmp_path / "l.jsonl")
    ledger.start_run(product_id="p", inputs_hash="h")
    ns = SimpleNamespace(
        ledger=ledger,
        budget=BuildBudget(wall_clock_s=wall),
        plan=SimpleNamespace(budget_class=""),
    )
    RoleRunner._note_plan_budget(ns)

    notes = [
        e for e in ledger.events()
        if e.kind is EventKind.NOTE and (e.payload or {}).get("plan_budget")
    ]
    assert notes
    assert notes[0].payload["budget_class"] == want_class
