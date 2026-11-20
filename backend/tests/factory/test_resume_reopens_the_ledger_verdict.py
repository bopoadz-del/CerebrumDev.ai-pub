"""A resumed run must never read as the finished failure it resumed from.

Continue appends a fresh ``RUN_STARTED`` to the same ledger. The status
derivation reads ``terminal_event()``, so a verdict that survives the new run
paints a live build as dead — live, the Floor showed "stopped by the owner"
while the resumed writer was still writing product. The last verdict is the
run's answer only until something reopens the run.
"""

from __future__ import annotations

import pytest

from app.factory.build.ledger import BuildLedger, EventKind
from app.factory.build_jobs import build_status


def _failed_ledger(path, *, product: str, inputs: str) -> BuildLedger:
    ledger = BuildLedger(path)
    ledger.start_run(product_id=product, inputs_hash=inputs)
    ledger.append(
        EventKind.RUN_FAILED,
        detail="stopped by the owner: stopped by the owner: owner stopped the build",
        payload={"outcome": "FAILED_ROLE_ERROR", "reason": "stopped_by_owner"},
    )
    return ledger


@pytest.mark.parametrize("product", ["resume-probe", "resume-probe-two"])
def test_a_failure_alone_is_the_runs_verdict(tmp_path, product):
    """Until something reopens the run, the last terminal IS the answer: a
    constant return would forget every finished build."""
    path = tmp_path / "build_ledger.jsonl"
    ledger = _failed_ledger(path, product=product, inputs="hash-1")
    terminal = ledger.terminal_event()
    assert terminal is not None
    assert terminal.kind is EventKind.RUN_FAILED
    assert ledger.succeeded() is False


@pytest.mark.parametrize("product", ["resume-probe", "resume-probe-two"])
def test_a_new_run_reopens_the_verdict(tmp_path, product):
    """Continue appends RUN_STARTED; the old verdict must stop being the
    answer, or the Floor keeps the failure card up while the writer works."""
    path = tmp_path / "build_ledger.jsonl"
    ledger = _failed_ledger(path, product=product, inputs="hash-1")
    ledger.start_run(product_id=product, inputs_hash="hash-1")
    assert ledger.terminal_event() is None
    assert ledger.succeeded() is False


@pytest.mark.parametrize("product", ["resume-probe", "resume-probe-two"])
def test_build_status_reads_a_resumed_run_as_building(tmp_path, product):
    """The status the UI reads: failed before the resume, building after —
    never failed while a live run is writing."""
    out = tmp_path / "build"
    out.mkdir()
    ledger = _failed_ledger(out / "build_ledger.jsonl", product=product, inputs="hash-1")
    assert build_status(out)["state"] == "failed"
    ledger.start_run(product_id=product, inputs_hash="hash-1")
    assert build_status(out)["state"] == "building"
