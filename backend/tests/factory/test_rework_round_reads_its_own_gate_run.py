"""A rework round's Store-gate verdict is read off the commit IT handed off.

Live 2026-10-08 (release cycle on 0e50fcc1, build/plt_3a04e7beceda4a74): round
1's gate failed audit_clean on 8cef0880; the writer fixed app/store.py and the
round-2 handoff pushed 17784ac8, whose gate run PASSED. The Factory recorded
SAME_FAILURE_TWICE one second after the handoff -- before that run had even
finished -- because the handoff note named only the branch, and
builds_fields_from_ledger let round 1's failure event (builds_sha=8cef0880)
outlive it. Round 2 re-read round 1's failed status.
"""

from __future__ import annotations

from app.factory.build.ledger import BuildLedger, EventKind
from app.factory.build.n3_store_gate import (
    HANDOFF_TO_N3,
    N3_STORE_GATE_FAILED,
    builds_fields_from_ledger,
)

BRANCH = "build/plt_0123456789abcdef"
ROUND_1_SHA = "8" * 40
ROUND_2_SHA = "1" * 40


def _ledger(tmp_path) -> BuildLedger:
    ledger = BuildLedger(tmp_path / "build_ledger.jsonl")
    ledger.start_run(product_id="p", inputs_hash="h")
    return ledger


def _round_one(ledger: BuildLedger) -> None:
    ledger.append(EventKind.NOTE, detail="handoff", payload={"handoff": HANDOFF_TO_N3, "builds_branch": BRANCH})
    ledger.append(
        EventKind.RUN_FAILED,
        detail="store-gate failed",
        payload={"honesty": N3_STORE_GATE_FAILED, "builds_sha": ROUND_1_SHA, "builds_branch": BRANCH},
    )
    ledger.append(EventKind.REWORK, detail="audit_clean back to the writer", payload={})


def test_a_new_handoff_without_a_sha_never_inherits_the_previous_rounds_sha(tmp_path):
    ledger = _ledger(tmp_path)
    _round_one(ledger)
    ledger.append(EventKind.NOTE, detail="handoff", payload={"handoff": HANDOFF_TO_N3, "builds_branch": BRANCH})

    fields = builds_fields_from_ledger(tmp_path)

    assert fields.get("builds_branch") == BRANCH
    # No sha named with the new target: the reader resolves the branch head.
    assert fields.get("builds_sha") != ROUND_1_SHA


def test_a_handoff_that_names_its_sha_is_the_target(tmp_path):
    ledger = _ledger(tmp_path)
    _round_one(ledger)
    ledger.append(
        EventKind.NOTE,
        detail="handoff",
        payload={"handoff": HANDOFF_TO_N3, "builds_branch": BRANCH, "builds_sha": ROUND_2_SHA},
    )
    # The run's own HANDOFF outcome lands after the note and names no target.
    ledger.append(EventKind.RUN_SUCCEEDED, detail="handed off", payload={"outcome": HANDOFF_TO_N3})

    assert builds_fields_from_ledger(tmp_path)["builds_sha"] == ROUND_2_SHA


def test_a_sha_recorded_alone_still_counts(tmp_path):
    # A note that records only the sha (the gate's own run evidence) is the
    # same target it already named; it is never dropped.
    ledger = _ledger(tmp_path)
    ledger.append(EventKind.NOTE, detail="handoff", payload={"handoff": HANDOFF_TO_N3, "builds_branch": BRANCH})
    ledger.append(EventKind.NOTE, detail="gate run", payload={"builds_sha": ROUND_2_SHA})

    assert builds_fields_from_ledger(tmp_path)["builds_sha"] == ROUND_2_SHA


def test_the_runner_records_the_sha_it_handed_off():
    # The handoff note carries the pushed head, so the reader never resolves
    # "the branch" while a newer push or an older verdict is in play.
    import inspect

    from app.factory.build import runner

    source = inspect.getsource(runner)
    start = source.index('"factory: hand off to store-gate"')
    block = source[start - 400 : start + 3000]
    assert '"builds_sha"' in block, "the handoff note must record the sha it pushed"
