"""The ceiling bump sees a CodeWhale writer's own progress.

Live 2026-10-08 (release cycle on 0e50fcc1, vineyard sess_e0208ddfe75642a5):
the writer reported STEP 7 and STEP 8 at 02:26, had 238 files on disk, and was
killed at 02:29 ("headless job exceeded 1800.0s" -- in fact the lifted 2700s
wall). The stage-2 inspect at 2700s decided ``await_cli`` with
``progressing=false``: a CodeWhale writer harvests its capabilities only when
its session ends, so caps_written stayed 0, and the one 2700->ceiling bump's
"visible work" test could not see the steps the writer was reporting.
"""

from __future__ import annotations

import inspect

from app.factory.build.budget_inspect import (
    CEILING_S,
    STAGE_2_S,
    inspect_build,
    inspect_decision,
)
from app.factory.build import codewhale_worker
from app.factory.build.ledger import BuildLedger, EventKind

# The worker's typed progress fields, by their wire keys. Resolved through
# getattr so this file imports on code that predates the constants: the test
# must FAIL ON ASSERTION there (the inspector ignores the fields), never on
# import -- an ImportError proves nothing about the defect.
STEPS_REPORTED = getattr(codewhale_worker, "STEPS_REPORTED", "steps_reported")
FILES_ON_DISK = getattr(codewhale_worker, "FILES_ON_DISK", "files_on_disk")

SOURCE = "codewhale_worker"


def _writer_in_flight(tmp_path) -> BuildLedger:
    ledger = BuildLedger(tmp_path / "build_ledger.jsonl")
    ledger.start_run(product_id="p", inputs_hash="h")
    ledger.append(
        EventKind.NOTE,
        detail="codewhale writer CLI started — model call in flight",
        payload={"model_call": True, "deadline_s": 1800.0, "provider": "x", "source": SOURCE},
    )
    ledger.append(
        EventKind.NOTE,
        detail="writer working",
        payload={"source": SOURCE, FILES_ON_DISK: 120, STEPS_REPORTED: 4},
    )
    # The stage-1 inspect (1800s) -- "this stage" starts after it.
    ledger.append(
        EventKind.NOTE,
        detail="inspect stage_1",
        payload={"budget_inspect": True, "kind": "budget_inspect", "decision": "continue_stage_2"},
    )
    return ledger


def _decide(ledger: BuildLedger):
    snap = inspect_build(ledger, None, {})
    return inspect_decision(
        elapsed_s=STAGE_2_S, current_wall_s=STAGE_2_S, snapshot=snap, stage="stage_2"
    )


def test_a_writer_reporting_steps_this_stage_gets_the_ceiling(tmp_path):
    ledger = _writer_in_flight(tmp_path)
    ledger.append(EventKind.NOTE, detail="writer: STEP 7", payload={"source": SOURCE, STEPS_REPORTED: 7})
    ledger.append(EventKind.NOTE, detail="writer: STEP 8", payload={"source": SOURCE, STEPS_REPORTED: 8})

    decided = _decide(ledger)

    assert decided["decision"] == "continue_ceiling", decided["reason"]
    assert decided["next_wall_s"] == CEILING_S
    assert decided.get("cli_steps_since_inspect") == 2


def test_files_growing_this_stage_is_visible_work(tmp_path):
    ledger = _writer_in_flight(tmp_path)
    ledger.append(
        EventKind.NOTE,
        detail="writer working",
        payload={"source": SOURCE, FILES_ON_DISK: 238, STEPS_REPORTED: 4},
    )

    decided = _decide(ledger)

    assert decided["decision"] == "continue_ceiling", decided["reason"]
    assert decided.get("cli_files_grown_since_inspect") == 118


def test_a_writer_silent_since_the_last_inspect_does_not_get_the_ceiling(tmp_path):
    # Heartbeats prove the process is alive, not that it is working: same
    # file count, no new step since stage 1 -> no silent 2h grant.
    ledger = _writer_in_flight(tmp_path)
    ledger.append(
        EventKind.NOTE,
        detail="writer working",
        payload={"source": SOURCE, FILES_ON_DISK: 120, STEPS_REPORTED: 4},
    )

    decided = _decide(ledger)

    assert decided["decision"] == "await_cli"
    assert decided["next_wall_s"] is None


def test_the_writer_relay_carries_the_progress_fields_to_the_ledger():
    from app.factory.build import roles_handlers

    source = inspect.getsource(roles_handlers._run_writer_via_codewhale_worker)
    assert "WRITER_PROGRESS_FIELDS" in source, (
        "relay_progress must copy the worker's typed progress fields onto the NOTE"
    )


def test_the_timeout_names_the_wall_that_fired():
    source = inspect.getsource(codewhale_worker)
    assert "deadline - wait_started" in source
