"""A run verdict is where the run STOPPED, not what it is doing now.

Live 21 Nov, ``plt_61e1edbf10e249e4``: the writer timed out at 05:34:54, the
owner pressed Continue, a rework was granted at 06:46:08, and the writer was
26 minutes into its fix pass -- while the Floor showed *WRITER failed* with
the fifty-minute-old ref. Two reasons, both read-side:

* the Continue door appends ``NOTE {resumed: True}``, not ``RUN_STARTED``
  (``build_jobs`` reattach block), and
* the granted ``REWORK`` carries ``decision``/``work_list``, not ``reopen``.

``terminal_event()`` knew neither key, so the ``RUN_FAILED`` stayed the last
thing it saw. Every door now goes through one rule: a phase, a gate, a rework
or a clone appended after a verdict reopens the run. A plain NOTE does not,
because the failure narrative is a NOTE appended right after ``RUN_FAILED``.
"""

from __future__ import annotations

import pytest

from app.factory.build.ledger import BuildLedger, BuildRole, EventKind
from app.factory.build_jobs import build_status, is_build_complete


def _ledger(tmp_path) -> BuildLedger:
    return BuildLedger(tmp_path / "build_ledger.jsonl")


def _failed_run(ledger: BuildLedger) -> None:
    """The shape every failed build leaves: phases, a timeout, a narrative."""
    ledger.start_run(product_id="product", inputs_hash="h")
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.COLLECTOR, detail="COLLECTOR")
    ledger.append(EventKind.GATE_PASSED, role=BuildRole.COLLECTOR, detail="ok")
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.CLONER, detail="CLONER")
    ledger.append(EventKind.GATE_PASSED, role=BuildRole.CLONER, detail="ok")
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER, detail="WRITER")
    ledger.append(
        EventKind.PHASE_ABORTED,
        role=BuildRole.WRITER,
        detail="codewhale_worker_failed: worker_timed_out",
    )
    ledger.append(EventKind.RUN_FAILED, role=BuildRole.WRITER, detail="WRITER failed")
    # Appended by the runner right after the verdict. It explains the
    # failure; it does not continue the run.
    ledger.append(EventKind.NOTE, role=BuildRole.WRITER, detail="failure narrative (ledger)")


def test_a_failed_run_with_only_its_narrative_is_still_failed(tmp_path):
    """The narrative NOTE must not reopen anything."""
    ledger = _ledger(tmp_path)
    _failed_run(ledger)
    terminal = ledger.terminal_event()
    assert terminal is not None
    assert terminal.kind is EventKind.RUN_FAILED
    assert not ledger.succeeded()


def test_the_continue_note_reopens_the_run(tmp_path):
    """What the Continue door actually appends -- a NOTE, not a RUN_STARTED."""
    ledger = _ledger(tmp_path)
    _failed_run(ledger)
    ledger.append(
        EventKind.NOTE,
        detail="RESUMED workspace=product__run2 phase=WRITER",
        payload={"resumed": True, "workspace": "product__run2", "phase": "WRITER"},
    )
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER, detail="WRITER")
    assert ledger.terminal_event() is None


def test_a_granted_rework_reopens_the_run(tmp_path):
    """The 21 Nov rework carried no ``reopen`` key; it still continues the run."""
    ledger = _ledger(tmp_path)
    _failed_run(ledger)
    ledger.append(
        EventKind.NOTE,
        detail="RESUMED workspace=product__run2 phase=WRITER",
        payload={"resumed": True},
    )
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER, detail="WRITER")
    ledger.append(
        EventKind.GATE_FAILED,
        role=BuildRole.WRITER,
        detail="money_assumed_without_a_brief",
    )
    ledger.append(
        EventKind.REWORK,
        role=BuildRole.WRITER,
        detail="WRITER round 1/1 (build 1/6, gate 'writer_contract')",
        payload={"gate": "writer_contract", "decision": {"round_gate": 1}},
    )
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER, detail="WRITER")
    assert ledger.terminal_event() is None


def test_a_gate_after_a_verdict_reopens_the_run(tmp_path):
    """You cannot pass a gate in a run that has finished."""
    ledger = _ledger(tmp_path)
    _failed_run(ledger)
    ledger.append(EventKind.GATE_PASSED, role=BuildRole.WRITER, detail="ok")
    assert ledger.terminal_event() is None


@pytest.mark.parametrize("ending", ["stopped by the owner", "WRITER failed again"])
def test_the_next_verdict_closes_the_reopened_run(tmp_path, ending):
    """Reopening must not hide the real ending: the newest verdict wins."""
    ledger = _ledger(tmp_path)
    _failed_run(ledger)
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER, detail="WRITER")
    ledger.append(EventKind.RUN_FAILED, role=BuildRole.WRITER, detail=ending)
    terminal = ledger.terminal_event()
    assert terminal is not None
    assert terminal.detail == ending


def test_a_successful_run_is_not_reopened_by_its_own_narrative(tmp_path):
    ledger = _ledger(tmp_path)
    ledger.start_run(product_id="product", inputs_hash="h")
    ledger.append(EventKind.RUN_SUCCEEDED, detail="code phase done")
    ledger.append(EventKind.NOTE, detail="receipt written")
    terminal = ledger.terminal_event()
    assert terminal is not None
    assert terminal.kind is EventKind.RUN_SUCCEEDED
    assert ledger.succeeded()


@pytest.mark.parametrize(
    "refusal", ["money_assumed_without_a_brief", "a_route_without_a_test"]
)
def test_the_floor_reads_a_resumed_rework_as_building(tmp_path, refusal):
    """The whole live shape, through the function the Floor actually calls.

    plt_61e1edbf10e249e4, 21 Nov: WRITER timed out at 05:34, the owner pressed
    Continue at 06:24 (a NOTE), the gate refused at 06:46 and a fix round was
    granted, and the writer kept writing -- while ``build_status`` served the
    05:34 verdict. Every door in that sequence is covered by one rule now.
    """
    out = tmp_path / "build"
    out.mkdir()
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id="product", inputs_hash="h")
    for phase in (BuildRole.COLLECTOR, BuildRole.CLONER):
        ledger.append(EventKind.PHASE_STARTED, role=phase, detail=phase.value)
        ledger.append(EventKind.GATE_PASSED, role=phase, detail="ok")
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER, detail="WRITER")
    ledger.append(
        EventKind.PHASE_ABORTED,
        role=BuildRole.WRITER,
        detail="codewhale_worker_failed: worker_timed_out: headless job exceeded its 1800s wall",
    )
    ledger.append(EventKind.RUN_FAILED, role=BuildRole.WRITER, detail="WRITER failed")
    ledger.append(EventKind.NOTE, role=BuildRole.WRITER, detail="failure narrative (ledger)")

    stopped = build_status(out)
    assert stopped["state"] == "failed"
    assert stopped.get("failed") is not None

    # Continue: the door appends a NOTE carrying ``resumed``, not a RUN_STARTED.
    ledger.append(
        EventKind.NOTE,
        detail="RESUMED workspace=product__run2 phase=WRITER",
        payload={"resumed": True, "workspace": "product__run2", "phase": "WRITER"},
    )
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER, detail="WRITER")
    # The gate refuses, a fix round is granted (no ``reopen`` key), and the
    # writer re-enters.
    ledger.append(
        EventKind.GATE_FAILED,
        role=BuildRole.WRITER,
        detail=refusal,
    )
    ledger.append(
        EventKind.REWORK,
        role=BuildRole.WRITER,
        detail="WRITER round 1/1 (build 1/6, gate 'writer_contract')",
        payload={"gate": "writer_contract", "source": "WRITER"},
    )
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER, detail="WRITER")

    live = build_status(out)
    assert live["state"] == "building"
    assert live["detail"] == "build in progress"
    # The rejection is still on the record -- the strip shows it as a rework,
    # which is why `failure` must survive while the run is live.
    assert live["failure"] is not None
    assert not live.get("failed")
    assert not live.get("failed_label")
    # The mid-build export guard reads this: a failed verdict disabled it.
    assert is_build_complete(out) is False
