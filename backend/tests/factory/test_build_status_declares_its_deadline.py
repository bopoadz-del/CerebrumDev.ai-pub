"""build-status declares the build's own deadline, so a client never guesses one.

Live 2026-10-08 (98d3a356, release cycle run 37732081706): smoke A read DEAD
"export zip" after its fixed client-side wait (~58 min) while the build was
healthy at "2/5 writer working -- 13m21s in, 200 files, 10 steps": four repros
plus the smoke shared a one-vCPU box. Owner rule: timeouts live at the
phase-wall ceiling only. The build already records every stage that opens a
new bounded wall (RUN_STARTED, a REWORK round, PILOT_OPENED, the N3 handoff);
build-status turns the latest of them into ``deadline_in_s``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.factory.build.ledger import BuildLedger, EventKind
from app.factory.build.n3_store_gate import HANDOFF_TO_N3, n3_wall_s
from app.factory.build.runner import BuildBudget
from app.factory.build_jobs import build_status, declared_deadline

CEILING = BuildBudget().hard_ceiling_s


def _iso(t: datetime) -> str:
    return t.isoformat().replace("+00:00", "Z")


class _E:
    def __init__(self, kind, ts, payload=None):
        self.kind, self.ts, self.payload = kind, _iso(ts), payload or {}


T0 = datetime(2026, 10, 8, 5, 56, 12, tzinfo=timezone.utc)


def test_a_running_build_is_bounded_by_its_run_ceiling():
    now = T0 + timedelta(minutes=58)
    d = declared_deadline([_E(EventKind.RUN_STARTED, T0)], now=now.timestamp())
    assert d["anchor"] == "RUN_STARTED"
    assert d["ceiling_s"] == CEILING
    # 58 minutes in is nowhere near the build's own 2 h ceiling.
    assert d["deadline_in_s"] == CEILING - 58 * 60


def test_a_rework_round_opens_a_new_bounded_wall():
    events = [
        _E(EventKind.RUN_STARTED, T0),
        _E(EventKind.REWORK, T0 + timedelta(minutes=100)),
    ]
    d = declared_deadline(events, now=(T0 + timedelta(minutes=110)).timestamp())
    assert d["anchor"] == "REWORK"
    assert d["deadline_in_s"] == CEILING - 10 * 60


def test_the_store_gate_handoff_is_bounded_by_the_gate_wall():
    events = [
        _E(EventKind.RUN_STARTED, T0),
        _E(EventKind.NOTE, T0 + timedelta(minutes=50), {"honesty": HANDOFF_TO_N3}),
    ]
    now = T0 + timedelta(minutes=55)
    d = declared_deadline(events, now=now.timestamp())
    # The later of the two declared ends wins: never shrink a live wall.
    run_end = CEILING - 55 * 60
    gate_end = n3_wall_s() - 5 * 60
    assert d["deadline_in_s"] == max(run_end, gate_end)


def test_a_passed_deadline_reads_negative():
    d = declared_deadline(
        [_E(EventKind.RUN_STARTED, T0)],
        now=(T0 + timedelta(seconds=CEILING + 30)).timestamp(),
    )
    assert d["deadline_in_s"] == -30


def test_no_anchor_declares_nothing():
    assert declared_deadline([], now=T0.timestamp()) is None


def test_build_status_carries_the_deadline(tmp_path):
    ledger = BuildLedger(tmp_path / "build_ledger.jsonl")
    ledger.start_run(product_id="p", inputs_hash="h")
    status = build_status(tmp_path)
    assert status["deadline"]["anchor"] == "RUN_STARTED"
    assert 0 < status["deadline"]["deadline_in_s"] <= CEILING
