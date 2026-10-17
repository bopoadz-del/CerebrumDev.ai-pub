"""A build must be re-openable after a gate failure it did not deserve.

`handoff_awaiting_n3` walked the ledger from the start and returned False on
ANY historical `N3_STORE_GATE_FAILED`, so one failed gate closed a build
forever. Live case (2026-09-17): a hospitality build sat at a genuine 12/12 on
its branch and could not be ingested, because the poller had recorded a FAILED
from a bogus 0/12 caused by a harness bug that was fixed minutes later.
`reseed_handoff_ledger` appends a fresh HANDOFF precisely to re-open a build;
the stale verdict must not outrank it.

The judgement is now made from the LAST handoff onward. These four cases pin
both directions: what must re-open, and what must stay closed.
"""
from __future__ import annotations

from pathlib import Path

from app.factory.build.authority import BuildRole
from app.factory.build.ledger import BuildLedger, EventKind
from app.factory.build.n3_store_gate import (
    HANDOFF_TO_N3,
    N3_STORE_GATE_FAILED,
    N3_STORE_GATE_GREEN,
    handoff_awaiting_n3,
)


def _ledger(tmp_path: Path, name: str):
    out = tmp_path / name
    out.mkdir(parents=True)
    lg = BuildLedger(out / "build_ledger.jsonl")
    lg.start_run(product_id="p", inputs_hash="h")
    return out, lg


def _handoff(lg) -> None:
    lg.append(
        EventKind.NOTE,
        role=BuildRole.WRITER,
        detail="HANDOFF_TO_N3: store-gate next",
        payload={"honesty": HANDOFF_TO_N3, "outcome": HANDOFF_TO_N3},
    )
    lg.append(
        EventKind.RUN_FAILED,
        role=BuildRole.STORE_MANAGER,
        detail="handoff",
        payload={"honesty": HANDOFF_TO_N3, "outcome": HANDOFF_TO_N3},
    )


def test_unjudged_handoff_is_awaiting(tmp_path):
    out, lg = _ledger(tmp_path, "plain")
    _handoff(lg)
    assert handoff_awaiting_n3(out) is True


def test_a_judged_failure_stays_closed(tmp_path):
    out, lg = _ledger(tmp_path, "failed")
    _handoff(lg)
    lg.append(
        EventKind.RUN_FAILED,
        role=BuildRole.STORE_MANAGER,
        detail="gate failed",
        payload={"honesty": N3_STORE_GATE_FAILED},
    )
    assert handoff_awaiting_n3(out) is False


def test_a_reseed_reopens_a_build_a_stale_failure_had_closed(tmp_path):
    """THE regression. Fails on the pre-fix implementation."""
    out, lg = _ledger(tmp_path, "reseed")
    _handoff(lg)
    lg.append(
        EventKind.RUN_FAILED,
        role=BuildRole.STORE_MANAGER,
        detail="gate failed",
        payload={"honesty": N3_STORE_GATE_FAILED},
    )
    _handoff(lg)  # reseed_handoff_ledger's fresh HANDOFF
    assert handoff_awaiting_n3(out) is True


def test_an_already_green_build_is_never_reopened(tmp_path):
    out, lg = _ledger(tmp_path, "green")
    _handoff(lg)
    lg.append(
        EventKind.RUN_SUCCEEDED,
        role=BuildRole.STORE_MANAGER,
        detail="green",
        payload={"honesty": N3_STORE_GATE_GREEN},
    )
    assert handoff_awaiting_n3(out) is False
