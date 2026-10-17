"""The Floor shows what the coding agent is doing, not just its last word.

Every STEP line the writer narrates already lands in the ledger as a NOTE,
but build-status only ever exposed ``last_event`` -- one sentence, replaced
every few minutes. From that an operator cannot tell a working agent from a
wedged one, which is the question the Floor exists to answer.
"""

from __future__ import annotations

from pathlib import Path

from app.factory.build.authority import BuildRole
from app.factory.build.ledger import BuildLedger, EventKind
from app.factory.build_jobs import _ACTIVITY_LOG_LINES, build_status

# Fake credentials for this test, generated per run (no value is stored in the repo).
_GENERATED_KEY_1 = "sk-" + __import__("secrets").token_hex(11)


def _started_ledger(tmp_path: Path) -> BuildLedger:
    out = tmp_path / "build"
    out.mkdir(parents=True, exist_ok=True)
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id="probe", inputs_hash="abc")
    return ledger


def test_the_writers_narration_reaches_the_floor_in_order(tmp_path):
    ledger = _started_ledger(tmp_path)
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER, detail="WRITER")
    for line in (
        "writer: STEP 0: inventory read",
        "writer: STEP 10: rework round 1",
        "writer: STEP 25: final pass on the staged tree",
    ):
        ledger.append(EventKind.NOTE, role=BuildRole.WRITER, detail=line)

    status = build_status(tmp_path / "build")
    log = status["activity_log"]

    assert [line["text"] for line in log] == [
        "writer: STEP 0: inventory read",
        "writer: STEP 10: rework round 1",
        "writer: STEP 25: final pass on the staged tree",
    ], "the whole pass must be visible, newest last"
    assert log[-1]["role"] == BuildRole.WRITER.value
    # last_event stays the newest line, so nothing that read it still works.
    assert status["last_event"] == "writer: STEP 25: final pass on the staged tree"


def test_a_long_pass_is_capped_to_the_tail(tmp_path):
    ledger = _started_ledger(tmp_path)
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER, detail="WRITER")
    for i in range(_ACTIVITY_LOG_LINES * 3):
        ledger.append(EventKind.NOTE, role=BuildRole.WRITER, detail=f"writer: STEP {i}")

    log = build_status(tmp_path / "build")["activity_log"]

    assert len(log) == _ACTIVITY_LOG_LINES
    assert log[-1]["text"] == f"writer: STEP {_ACTIVITY_LOG_LINES * 3 - 1}"


def test_secrets_are_not_rendered_into_the_log(tmp_path):
    """F5: the log is sanitized on the same path last_event is."""
    ledger = _started_ledger(tmp_path)
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER, detail="WRITER")
    ledger.append(
        EventKind.NOTE,
        role=BuildRole.WRITER,
        detail="writer: calling with Authorization: Bearer " + _GENERATED_KEY_1 + "",
    )

    log = build_status(tmp_path / "build")["activity_log"]

    assert log, "the note should still be shown, with the secret removed"
    assert _GENERATED_KEY_1 not in log[-1]["text"], log[-1]["text"]


def test_no_notes_means_an_empty_log_not_a_missing_key(tmp_path):
    ledger = _started_ledger(tmp_path)
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER, detail="WRITER")

    status = build_status(tmp_path / "build")

    assert status["activity_log"] == []
