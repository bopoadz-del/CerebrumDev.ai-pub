"""A re-entered run reads as running, and its own stop as its own.

Try again appends the typed resume NOTE, starts the runner thread and answers
with the build status at once -- before the thread has written a phase event.
The old RUN_FAILED was still the newest verdict then, so the reply, the build
card and the chat read a live run as the failure it resumed from. And when the
owner stopped that run, which writes no narrative of its own, the status
explained the stop with the earlier attempt's narrative.

Every test drives the build-status route (or the status read it calls) over a
real ledger; the live thread is a real thread registered for the workspace.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.factory.build.failure_narrative as fn
import app.factory.build.failure_record as fr
from app.core.auth import DEV_LOCAL_TENANT
from app.core.session_store import create_session, get_session, update_session
from app.factory.build.authority import BuildRole
from app.factory.build.ledger import BuildLedger, EventKind
from app.factory.build_jobs import (
    _RUNNER_THREADS,
    _RUNNER_THREADS_GUARD,
    _reentered_live,
    build_status,
    register_runner_thread,
    runner_key,
)
from app.main import app


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("ENV", "test")
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)
    monkeypatch.setenv("ALLOW_ANONYMOUS_DEV", "1")
    return TestClient(app)


@pytest.fixture()
def live_thread():
    """A real runner thread for a workspace, released and unregistered after."""
    release = threading.Event()
    keys: list = []

    def start(out: Path) -> threading.Thread:
        thread = threading.Thread(target=release.wait, daemon=True)
        register_runner_thread(out, thread)
        thread.start()
        keys.append(runner_key(out))
        return thread

    yield start
    release.set()
    with _RUNNER_THREADS_GUARD:
        for key in keys:
            _RUNNER_THREADS.pop(key, None)


def _failed_at_writer(out: Path, reason: str, failure_kind: str, narrative: str) -> BuildLedger:
    """A run that passed COLLECTOR and CLONER, then failed at the WRITER."""
    out.mkdir(parents=True, exist_ok=True)
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id="p", inputs_hash="h")
    for role in (BuildRole.COLLECTOR, BuildRole.CLONER):
        ledger.append(EventKind.PHASE_STARTED, role=role)
        ledger.append(EventKind.GATE_PASSED, role=role, payload={"gate": role.value})
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER)
    ledger.append(
        EventKind.GATE_FAILED,
        role=BuildRole.WRITER,
        detail=f"{reason}: the writer did not finish",
        payload={"reason": reason, "location": "WRITER", "failure_kind": failure_kind},
    )
    ledger.append(
        EventKind.RUN_FAILED,
        role=BuildRole.WRITER,
        detail=f"WRITER failed: {reason}",
        payload={"outcome": "FAILED_GATE", "reason": reason},
    )
    fn.attach(ledger, fn.Narrative(text=narrative, source=fn.SOURCE_LEDGER, cites=[]))
    return ledger


def _resume(ledger: BuildLedger, phase: "str | None") -> None:
    """The NOTE generate_product appends before it starts the runner thread."""
    ledger.append(
        EventKind.NOTE,
        detail=f"RESUMED workspace=p phase={phase}",
        payload={"resumed": True, "workspace": "p", "phase": phase},
    )


def _owner_stop(ledger: BuildLedger) -> None:
    """What the runner writes when the owner stops the re-entered WRITER."""
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER)
    ledger.append(
        EventKind.PHASE_ABORTED,
        role=BuildRole.WRITER,
        detail="stopped by the owner: stop",
        payload={"reason": "stopped_by_owner"},
    )
    ledger.append(
        EventKind.RUN_FAILED,
        role=BuildRole.WRITER,
        detail="stopped by the owner during WRITER",
        payload={"outcome": "STOPPED_BY_OWNER", "decision": {"reason": "stopped_by_owner"}},
    )


def _session(sid: str, out: Path) -> str:
    create_session(sid, DEV_LOCAL_TENANT)
    session = get_session(sid)
    session.product_design.generation = {
        "product_id": "p",
        "output_dir": str(out),
        "engine": "runner",
    }
    update_session(sid, session)
    return sid


# ---------------------------------------------------------------------------
# After Try again: running at step 3 on the first poll.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "reason, failure_kind, phase",
    [
        ("codewhale_worker_failed", "out_of_time", "WRITER"),
        ("writer_contract", "", None),
    ],
)
def test_first_poll_after_try_again_says_running_at_step_three(
    client, tmp_path, live_thread, reason, failure_kind, phase
):
    out = tmp_path / f"ws_{reason}"
    ledger = _failed_at_writer(out, reason, failure_kind, f"the writer failed ({reason})")
    sid = _session(f"sess_reenter_{reason}", out)
    assert (
        client.get(f"/v1/sessions/{sid}/product/build-status").json()["build"]["state"] == "failed"
    )

    _resume(ledger, phase)
    live_thread(out)
    build = client.get(f"/v1/sessions/{sid}/product/build-status").json()["build"]

    assert build["state"] == "building"
    assert build["current_phase"]["id"] == "WRITER"
    assert build["phase_index"] == 3
    assert build["failure"] is None
    assert "failed" not in build and "stop" not in build


@pytest.mark.parametrize(
    "reason, resumed, thread",
    [
        # The resume was refused at the door: the NOTE is there, no thread.
        ("codewhale_worker_failed", True, False),
        # A thread is still finishing after its OWN verdict (narrating it):
        # no resume came after that verdict.
        ("writer_contract", False, True),
    ],
)
def test_a_failed_run_without_a_live_re_entry_stays_failed(
    client, tmp_path, live_thread, reason, resumed, thread
):
    out = tmp_path / f"ws_{reason}"
    ledger = _failed_at_writer(out, reason, "", f"the writer failed ({reason})")
    sid = _session(f"sess_noreenter_{reason}", out)
    if resumed:
        _resume(ledger, "WRITER")
    if thread:
        live_thread(out)

    build = client.get(f"/v1/sessions/{sid}/product/build-status").json()["build"]

    assert build["state"] == "failed"
    assert build["failure"]["code"] == reason


@pytest.mark.parametrize(
    "terminal_kind",
    [None, EventKind.RUN_SUCCEEDED],
)
def test_only_a_failed_verdict_is_re_entered(tmp_path, live_thread, terminal_kind):
    out = tmp_path / "ws"
    out.mkdir()
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id="p", inputs_hash="h")
    if terminal_kind is not None:
        ledger.append(terminal_kind, payload={"outcome": "SUCCESS"})
    _resume(ledger, "WRITER")
    live_thread(out)

    assert _reentered_live(ledger.events(), ledger.terminal_event(), out) is False


# ---------------------------------------------------------------------------
# After an owner stop of the re-entered run: stopped by you, its own story.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "reason, failure_kind",
    [
        ("codewhale_worker_failed", "out_of_time"),
        ("writer_contract", ""),
    ],
)
def test_owner_stop_after_try_again_reads_stopped_by_you(client, tmp_path, reason, failure_kind):
    out = tmp_path / f"ws_{reason}"
    old_story = f"the writer failed ({reason}) and the run came back marked FAILED"
    ledger = _failed_at_writer(out, reason, failure_kind, old_story)
    sid = _session(f"sess_ownerstop_{reason}", out)
    _resume(ledger, "WRITER")
    _owner_stop(ledger)

    build = client.get(f"/v1/sessions/{sid}/product/build-status").json()["build"]

    assert build["state"] == "failed"
    assert build["stop"]["kind"] == fr.STOP_USER_STOPPED
    narrative = build["failure_narrative"] or {}
    assert narrative.get("text") != old_story
    assert reason not in narrative.get("text", "")
    assert "stopped_by_owner" in narrative.get("text", "")


@pytest.mark.parametrize(
    "reason",
    ["codewhale_worker_failed", "writer_contract"],
)
def test_a_narrative_written_after_the_newest_verdict_is_still_read(tmp_path, reason):
    """The rule must not fire on the run's own narrative: no re-entry, or a
    re-entry that failed again and narrated its new verdict."""
    out = tmp_path / f"ws_{reason}"
    ledger = _failed_at_writer(out, reason, "", f"first story ({reason})")
    assert fn.read_narrative(out)["text"] == f"first story ({reason})"

    _resume(ledger, "WRITER")
    _owner_stop(ledger)
    fn.attach(
        ledger, fn.Narrative(text=f"second story ({reason})", source=fn.SOURCE_LEDGER, cites=[])
    )

    assert fn.read_narrative(out)["text"] == f"second story ({reason})"


@pytest.mark.parametrize(
    "extra_notes",
    [0, 2],
)
def test_a_ledger_with_no_verdict_facts_has_no_narrative(tmp_path, extra_notes):
    out = tmp_path / f"ws_{extra_notes}"
    out.mkdir()
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id="p", inputs_hash="h")
    for i in range(extra_notes):
        ledger.append(EventKind.NOTE, detail=f"note {i}")

    assert fn.read_narrative(out) is None


@pytest.mark.parametrize(
    "reason",
    ["codewhale_worker_failed", "writer_contract"],
)
def test_build_status_read_matches_the_route_after_re_entry(tmp_path, live_thread, reason):
    """The status the Try-again reply carries is the same read: running."""
    out = tmp_path / f"ws_{reason}"
    ledger = _failed_at_writer(out, reason, "", "x")
    _resume(ledger, "WRITER")
    live_thread(out)

    st = build_status(out)

    assert (st["state"], st["phase_index"], st["failure"]) == ("building", 3, None)


# ---------------------------------------------------------------------------
# A run that ended WITHOUT a run verdict (a deploy kill, an overdue model call,
# a crash) and was re-entered: its old phase failure is history from the
# moment the re-entry's own phase starts, and so is a call it left open.
# ---------------------------------------------------------------------------


def _killed_at_writer(out: Path, reason: str, failure_kind: str) -> BuildLedger:
    """WRITER judged failed, then the process died before the run verdict."""
    out.mkdir(parents=True, exist_ok=True)
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id="p", inputs_hash="h")
    for role in (BuildRole.COLLECTOR, BuildRole.CLONER):
        ledger.append(EventKind.PHASE_STARTED, role=role)
        ledger.append(EventKind.GATE_PASSED, role=role, payload={"gate": role.value})
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER)
    ledger.append(
        EventKind.GATE_FAILED,
        role=BuildRole.WRITER,
        detail=f"{reason}: the writer did not finish",
        payload={"reason": reason, "location": "WRITER", "failure_kind": failure_kind},
    )
    return ledger


def _old_open_call(out: Path, ts: str, deadline_s: int) -> None:
    """A writer model call opened at ``ts`` and never closed (the kill)."""
    BuildLedger(out / "build_ledger.jsonl", clock=lambda: ts).append(
        EventKind.NOTE,
        role=BuildRole.WRITER,
        detail="codewhale writer CLI started — model call in flight",
        payload={"model_call": True, "deadline_s": deadline_s},
    )


@pytest.mark.parametrize(
    "reason, failure_kind",
    [
        ("codewhale_worker_failed", "out_of_time"),
        ("writer_contract", ""),
    ],
)
def test_re_entered_phase_drops_a_failure_no_verdict_closed(
    client, tmp_path, live_thread, reason, failure_kind
):
    out = tmp_path / f"ws_{reason}"
    ledger = _killed_at_writer(out, reason, failure_kind)
    sid = _session(f"sess_killed_{reason}", out)
    _resume(ledger, "WRITER")
    live_thread(out)
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER)

    build = client.get(f"/v1/sessions/{sid}/product/build-status").json()["build"]

    assert build["state"] == "building"
    assert build["phase_index"] == 3
    assert build["failure"] is None
    writer = next(row for row in build["phase_trail"] if row["phase"] == "WRITER")
    assert writer["outcome"] == "running"


@pytest.mark.parametrize(
    "ts, deadline_s",
    [
        ("2026-11-27T06:00:00+00:00", 600),
        ("2026-11-26T20:00:00+00:00", 3553),
    ],
)
def test_a_call_the_killed_attempt_left_open_is_not_this_runs_call(
    client, tmp_path, live_thread, ts, deadline_s
):
    """Candidate (a): the old call outlived its wall; the re-entry is not overdue."""
    out = tmp_path / f"ws_{deadline_s}"
    out.mkdir()
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id="p", inputs_hash="h")
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER)
    _old_open_call(out, ts, deadline_s)
    sid = _session(f"sess_opencall_{deadline_s}", out)
    _resume(ledger, "WRITER")
    live_thread(out)

    build = client.get(f"/v1/sessions/{sid}/product/build-status").json()["build"]

    assert build["state"] == "building"
    assert not build.get("model_call_in_progress")
    assert "timed out" not in str(build.get("detail") or "")


@pytest.mark.parametrize(
    "ts, deadline_s",
    [
        ("2026-11-27T06:00:00+00:00", 600),
        ("2026-11-26T20:00:00+00:00", 3553),
    ],
)
def test_an_open_call_with_no_re_entry_is_still_read_overdue(
    client, tmp_path, live_thread, ts, deadline_s
):
    """Must not fire: the same open call, no resume, a live worker -> overdue."""
    out = tmp_path / f"ws_{deadline_s}"
    out.mkdir()
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id="p", inputs_hash="h")
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER)
    _old_open_call(out, ts, deadline_s)
    sid = _session(f"sess_overdue_{deadline_s}", out)
    live_thread(out)

    build = client.get(f"/v1/sessions/{sid}/product/build-status").json()["build"]

    assert build["state"] != "building"
    assert "timed out" in str(build.get("detail") or "")


@pytest.mark.parametrize(
    "reason, verdict_first",
    [
        ("codewhale_worker_failed", True),
        ("writer_contract", False),
    ],
)
def test_owner_stop_before_the_re_entry_starts_a_phase_reads_stopped_by_you(
    client, tmp_path, reason, verdict_first
):
    """Fact 4: a stop in the slot wait writes no PHASE_ABORTED; the old WRITER
    failure must not be the row the stop is explained by."""
    out = tmp_path / f"ws_{reason}"
    if verdict_first:
        ledger = _failed_at_writer(out, reason, "out_of_time", "old story")
    else:
        ledger = _killed_at_writer(out, reason, "")
    sid = _session(f"sess_slotstop_{reason}", out)
    _resume(ledger, "WRITER")
    ledger.append(
        EventKind.RUN_FAILED,
        detail="stopped by the owner during the build",
        payload={"outcome": "STOPPED_BY_OWNER", "decision": {"reason": "stopped_by_owner"}},
    )

    build = client.get(f"/v1/sessions/{sid}/product/build-status").json()["build"]

    assert build["state"] == "failed"
    assert build["stop"]["kind"] == fr.STOP_USER_STOPPED
    assert build["failure"]["code"] != reason


@pytest.mark.parametrize(
    "note_payload",
    [{"resumed": False}, {"handoff": False, "phase": "WRITER"}],
)
def test_a_note_that_is_not_a_resume_keeps_the_failure_standing(tmp_path, note_payload):
    """Must not fire: inside one attempt a later phase start (a send-back)
    keeps the failure that sent it back standing."""
    from app.factory.build_jobs import _trail

    out = tmp_path / "ws"
    ledger = _killed_at_writer(out, "writer_contract", "")
    ledger.append(EventKind.NOTE, detail="n", payload=note_payload)
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.TESTER)

    _rows, _history, standing = _trail(ledger.events())

    assert standing is not None and standing["reason"] == "writer_contract"


@pytest.mark.parametrize(
    "old_reason, new_reason",
    [
        ("codewhale_worker_failed", "writer_contract"),
        ("writer_contract", "writer_no_output"),
    ],
)
def test_the_re_entrys_own_failure_stands_at_its_verdict(client, tmp_path, old_reason, new_reason):
    """Must not fire: once the re-entry starts a phase, a failure it records
    is the run's own and the verdict after it is explained by it."""
    out = tmp_path / f"ws_{old_reason}"
    ledger = _killed_at_writer(out, old_reason, "")
    sid = _session(f"sess_ownfail_{old_reason}", out)
    _resume(ledger, "WRITER")
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER)
    ledger.append(
        EventKind.GATE_FAILED,
        role=BuildRole.WRITER,
        detail=f"{new_reason}: again",
        payload={"reason": new_reason, "location": "WRITER"},
    )
    ledger.append(
        EventKind.RUN_FAILED,
        role=BuildRole.WRITER,
        detail=f"WRITER failed: {new_reason}",
        payload={"outcome": "FAILED_GATE", "reason": new_reason},
    )

    build = client.get(f"/v1/sessions/{sid}/product/build-status").json()["build"]

    assert build["state"] == "failed"
    assert build["failure"]["code"] == new_reason


# ---------------------------------------------------------------------------
# The live thread must be the one the resume started. The failed run's own
# thread can still be alive after its verdict (narrating it); a Try again
# refused at the door after its NOTE started nothing, so the run stays failed
# and the next Try again starts a runner.
# ---------------------------------------------------------------------------


def _failed_while_its_thread_lives(
    out: Path, reason: str, live_thread, inputs_hash: str = "h"
) -> BuildLedger:
    """A WRITER failure whose runner thread was started before the verdict
    and is still alive after it."""
    out.mkdir(parents=True, exist_ok=True)
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id="p", inputs_hash=inputs_hash)
    for role in (BuildRole.COLLECTOR, BuildRole.CLONER):
        ledger.append(EventKind.PHASE_STARTED, role=role)
        ledger.append(EventKind.GATE_PASSED, role=role, payload={"gate": role.value})
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER)
    live_thread(out)
    ledger.append(
        EventKind.GATE_FAILED,
        role=BuildRole.WRITER,
        detail=f"{reason}: the writer did not finish",
        payload={"reason": reason, "location": "WRITER"},
    )
    ledger.append(
        EventKind.RUN_FAILED,
        role=BuildRole.WRITER,
        detail=f"WRITER failed: {reason}",
        payload={"outcome": "FAILED_GATE", "reason": reason},
    )
    return ledger


@pytest.mark.parametrize("reason", ["codewhale_worker_failed", "writer_contract"])
def test_the_failed_runs_own_thread_is_not_the_re_entry(client, tmp_path, live_thread, reason):
    out = tmp_path / f"ws_{reason}"
    ledger = _failed_while_its_thread_lives(out, reason, live_thread)
    sid = _session(f"sess_oldthread_{reason}", out)
    _resume(ledger, "WRITER")

    build = client.get(f"/v1/sessions/{sid}/product/build-status").json()["build"]
    assert (build["state"], build["failure"]["code"]) == ("failed", reason)
    assert _reentered_live(ledger.events(), ledger.terminal_event(), out) is False

    # Must fire: a thread started after the NOTE is the re-entry.
    live_thread(out)
    build = client.get(f"/v1/sessions/{sid}/product/build-status").json()["build"]
    assert (build["state"], build["failure"]) == ("building", None)


@pytest.mark.parametrize("door", ["cli_unready", "builds_unavailable"])
def test_a_try_again_refused_at_the_door_stays_failed_and_the_next_one_starts(
    tmp_path, monkeypatch, live_thread, door
):
    """Public path: start_runner_build over a real ledger while the failed
    run's own thread is still alive."""
    from app.factory.blueprint import CapabilitySpec, ProductBlueprint
    from app.factory.build import coder_session, store_override
    from app.factory.build.runner import blueprint_hash
    from app.factory.build_jobs import start_runner_build

    monkeypatch.setenv("FACTORY_BUILD_ENGINE", "runner")
    monkeypatch.setenv("ENV", "test")
    bp = ProductBlueprint(
        schema_version="product_blueprint.v1",
        product_id=f"p-{door}",
        product_name="P",
        vertical="v",
        summary="s",
        capabilities=[
            CapabilitySpec(
                id="vault", description="vault", block_ids=["storage"], strategy_hint="REUSE"
            )
        ],
    )
    out = tmp_path / door / "ws"
    _failed_while_its_thread_lives(out, "writer_contract", live_thread, blueprint_hash(bp))

    def refuse():
        raise coder_session.CodeCliUnavailable("no coding CLI session")

    if door == "cli_unready":
        monkeypatch.setattr(coder_session, "raise_if_cli_session_unready", refuse)
    else:
        monkeypatch.setattr(coder_session, "raise_if_cli_session_unready", lambda: None)
        monkeypatch.setattr(
            store_override, "builds_unavailable_reason", lambda _root: "proofs cannot be kept"
        )
    with pytest.raises(coder_session.CodeCliUnavailable):
        start_runner_build(bp, out)

    st = build_status(out)
    assert (st["state"], st["failure"]["code"]) == ("failed", "writer_contract")

    monkeypatch.setattr(coder_session, "raise_if_cli_session_unready", lambda: None)
    monkeypatch.setattr(store_override, "builds_unavailable_reason", lambda _root: None)
    held = threading.Event()
    started: list = []

    def hold(*_a, **_k):
        started.append(1)
        held.wait(timeout=5)

    monkeypatch.setattr("app.factory.build_jobs._run", hold)
    try:
        result = start_runner_build(bp, out)
        assert result["already_running"] is False
        assert result["build"]["state"] == "building"
        for _ in range(100):
            if started:
                break
            threading.Event().wait(0.02)
        assert started == [1]
    finally:
        held.set()


@pytest.mark.parametrize(
    "kind, expected",
    [(EventKind.GATE_PASSED, False), (EventKind.PHASE_STARTED, False), (EventKind.NOTE, True)],
)
def test_only_a_resume_note_re_enters(tmp_path, live_thread, kind, expected):
    """An event that is not a NOTE carrying ``resumed`` is not a resume,
    even with a thread started after it. Read against the RUN_FAILED verdict
    itself: through the ledger such an event also reopens the run."""
    out = tmp_path / f"ws_{kind.value}"
    ledger = _failed_at_writer(out, "writer_contract", "", "x")
    verdict = [e for e in ledger.events() if e.kind is EventKind.RUN_FAILED][-1]
    ledger.append(kind, role=BuildRole.WRITER, payload={"resumed": True, "phase": "WRITER"})
    live_thread(out)

    assert _reentered_live(ledger.events(), verdict, out) is expected


@pytest.mark.parametrize(
    "thread_after_resumes, expected",
    [(1, False), (2, True)],
)
def test_the_runner_must_follow_the_newest_resume(
    tmp_path, live_thread, thread_after_resumes, expected
):
    """Two resumes after the verdict: only a thread started after the newer
    one is that resume's runner."""
    out = tmp_path / f"ws_{thread_after_resumes}"
    ledger = _failed_at_writer(out, "writer_contract", "", "x")
    for n in (1, 2):
        _resume(ledger, "WRITER")
        if n == thread_after_resumes:
            live_thread(out)

    assert _reentered_live(ledger.events(), ledger.terminal_event(), out) is expected


# ---------------------------------------------------------------------------
# The re-entry's own thread, once it has exited without a phase event, is no
# live run; a thread registered before the workspace had a ledger was started
# after no event at all, so it is no resume's runner either.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("reason", ["codewhale_worker_failed", "writer_contract"])
def test_a_re_entry_whose_thread_has_exited_reads_failed(client, tmp_path, reason):
    from app.factory.build_jobs import _RUNNER_STARTED_AFTER, live_runner_thread

    out = tmp_path / f"ws_{reason}"
    ledger = _failed_at_writer(out, reason, "", f"the writer failed ({reason})")
    sid = _session(f"sess_exited_{reason}", out)
    _resume(ledger, "WRITER")
    finished = threading.Thread(target=lambda: None, daemon=True)
    register_runner_thread(out, finished)
    finished.start()
    finished.join(timeout=5)
    assert not finished.is_alive()
    assert runner_key(out) in _RUNNER_STARTED_AFTER

    assert _reentered_live(ledger.events(), ledger.terminal_event(), out) is False
    assert live_runner_thread(out) is None
    # A runner that has exited leaves no start mark behind.
    assert runner_key(out) not in _RUNNER_STARTED_AFTER
    build = client.get(f"/v1/sessions/{sid}/product/build-status").json()["build"]
    assert (build["state"], build["failure"]["code"]) == ("failed", reason)


@pytest.mark.parametrize("reason", ["codewhale_worker_failed", "writer_contract"])
def test_a_thread_registered_before_any_ledger_is_not_the_re_entry(
    client, tmp_path, live_thread, reason
):
    out = tmp_path / f"ws_{reason}"
    out.mkdir()
    live_thread(out)
    ledger = _failed_at_writer(out, reason, "", f"the writer failed ({reason})")
    sid = _session(f"sess_noledger_{reason}", out)
    _resume(ledger, "WRITER")

    assert _reentered_live(ledger.events(), ledger.terminal_event(), out) is False
    build = client.get(f"/v1/sessions/{sid}/product/build-status").json()["build"]
    assert (build["state"], build["failure"]["code"]) == ("failed", reason)


# ---------------------------------------------------------------------------
# A narrative explains ONE verdict: the newest among the events it was
# composed from. The failed attempt's thread narrates with a model call; the
# owner can press Try again and Stop before it returns, so its narrative
# lands AFTER the owner-stop verdict -- and must not be that stop's story.
# ---------------------------------------------------------------------------

_OWNER_STOP = {"outcome": "STOPPED_BY_OWNER", "decision": {"reason": "stopped_by_owner"}}


def _failed_unnarrated(out: Path, reason: str) -> BuildLedger:
    """WRITER failed and the run verdict is written; no narrative yet."""
    ledger = _killed_at_writer(out, reason, "")
    ledger.append(
        EventKind.RUN_FAILED,
        role=BuildRole.WRITER,
        detail=f"WRITER failed: {reason}",
        payload={"outcome": "FAILED_GATE", "reason": reason},
    )
    return ledger


@pytest.mark.parametrize("reason", ["codewhale_worker_failed", "writer_contract"])
def test_a_narration_that_outlasts_try_again_and_stop_does_not_explain_the_stop(
    client, tmp_path, monkeypatch, live_thread, reason
):
    """The runner's own write path (``_attach_failure_narrative``): the model
    call is in flight while the owner re-enters the run and stops it."""
    from types import SimpleNamespace

    from app.factory.build.runner import RoleRunner

    monkeypatch.setenv("FACTORY_FOREMAN", "on")
    out = tmp_path / f"ws_{reason}"
    ledger = _failed_unnarrated(out, reason)
    sid = _session(f"sess_latenarr_{reason}", out)
    url = f"/v1/sessions/{sid}/product/build-status"
    during: list = []

    def model_call(_messages):
        if not during:
            _resume(ledger, "WRITER")
            live_thread(out)
            during.append(client.get(url).json()["build"]["state"])
            ledger.append(
                EventKind.RUN_FAILED,
                detail="stopped by the owner during the build",
                payload=_OWNER_STOP,
            )
        return {"sentences": []}

    RoleRunner._attach_failure_narrative(
        SimpleNamespace(ledger=ledger, narrative_llm=model_call), BuildRole.WRITER
    )

    events = ledger.events()
    verdicts = [e.seq for e in events if e.kind is EventKind.RUN_FAILED]
    old = [e for e in events if (e.payload or {}).get(fn.NARRATIVE_KEY)][-1]
    assert during == ["building"]
    assert old.seq > verdicts[-1] > verdicts[0]
    assert old.payload[fn.VERDICT_KEY] == verdicts[0]
    assert reason in old.payload[fn.NARRATIVE_KEY]["text"]

    build = client.get(url).json()["build"]
    assert build["state"] == "failed"
    assert build["stop"]["kind"] == fr.STOP_USER_STOPPED
    text = (build["failure_narrative"] or {}).get("text", "")
    assert reason not in text
    assert "stopped_by_owner" in text


@pytest.mark.parametrize("reason", ["codewhale_worker_failed", "writer_contract"])
def test_an_unnamed_narrative_after_an_owner_stop_is_not_read_for_it(client, tmp_path, reason):
    """A ledger from before narratives named their verdict: the failed
    attempt had no narrative when the owner stop was written, so a narrative
    after the stop cannot be told from the earlier attempt's."""
    out = tmp_path / f"ws_{reason}"
    old_story = f"the writer failed ({reason}) and the run came back marked FAILED"
    ledger = _failed_unnarrated(out, reason)
    sid = _session(f"sess_unnamed_{reason}", out)
    _resume(ledger, "WRITER")
    ledger.append(
        EventKind.RUN_FAILED, detail="stopped by the owner during the build", payload=_OWNER_STOP
    )
    fn.attach(ledger, fn.Narrative(text=old_story, source=fn.SOURCE_LEDGER, cites=[]))

    build = client.get(f"/v1/sessions/{sid}/product/build-status").json()["build"]

    assert build["stop"]["kind"] == fr.STOP_USER_STOPPED
    text = (build["failure_narrative"] or {}).get("text", "")
    assert text != old_story and reason not in text
    assert "stopped_by_owner" in text


@pytest.mark.parametrize("old_lands_last", [True, False])
def test_each_narrative_is_read_for_the_verdict_it_names(tmp_path, old_lands_last):
    """Must fire: the re-entry failed again and narrated its own verdict;
    the earlier attempt's narrative lands before or after it."""
    out = tmp_path / f"ws_{old_lands_last}"
    ledger = _failed_unnarrated(out, "codewhale_worker_failed")
    first = fn.newest_verdict_seq(ledger.events())
    _resume(ledger, "WRITER")
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER)
    second = ledger.append(
        EventKind.RUN_FAILED,
        role=BuildRole.WRITER,
        detail="WRITER failed: writer_contract",
        payload={"outcome": "FAILED_GATE", "reason": "writer_contract"},
    ).seq
    stories = [("first story", first), ("second story", second)]
    for text, seq in reversed(stories) if old_lands_last else stories:
        fn.attach(
            ledger, fn.Narrative(text=text, source=fn.SOURCE_LEDGER, cites=[]), verdict_seq=seq
        )

    assert first != second
    assert fn.read_narrative(out)["text"] == "second story"


@pytest.mark.parametrize("verdicts", [0, 2])
def test_the_newest_verdict_is_the_one_a_narrative_names(tmp_path, verdicts):
    out = tmp_path / f"ws_{verdicts}"
    ledger = _killed_at_writer(out, "writer_contract", "")
    seqs = [
        ledger.append(EventKind.RUN_FAILED, role=BuildRole.WRITER, payload={"n": n}).seq
        for n in range(verdicts)
    ]
    ledger.append(EventKind.NOTE, detail="after")

    assert fn.newest_verdict_seq(ledger.events()) == (seqs[-1] if seqs else None)


@pytest.mark.parametrize("reason", ["codewhale_worker_failed", "writer_contract"])
def test_a_narrative_from_before_a_new_run_start_is_not_read(tmp_path, reason):
    """Must not fire across runs: a narrative belongs to the run it was
    written in; after a new RUN_STARTED it explains nothing yet."""
    out = tmp_path / f"ws_{reason}"
    story = f"first story ({reason})"
    ledger = _failed_at_writer(out, reason, "", story)
    assert fn.read_narrative(out)["text"] == story

    ledger.start_run(product_id="p", inputs_hash="h2")

    assert fn.read_narrative(out)["text"] != story


@pytest.mark.parametrize("kind", [EventKind.RUN_SUCCEEDED, EventKind.RUN_FAILED])
def test_a_newer_verdict_of_either_kind_ends_the_older_story(tmp_path, kind):
    """A run that succeeded after its failure is no longer explained by that
    failure's narrative, exactly as one that failed again is not."""
    out = tmp_path / f"ws_{kind.value}"
    ledger = _failed_unnarrated(out, "writer_contract")
    fn.attach(
        ledger,
        fn.Narrative(text="first story", source=fn.SOURCE_LEDGER, cites=[]),
        verdict_seq=fn.newest_verdict_seq(ledger.events()),
    )
    assert fn.read_narrative(out)["text"] == "first story"

    newer = ledger.append(kind, role=BuildRole.WRITER, payload={"outcome": kind.value})

    assert fn.newest_verdict_seq(ledger.events()) == newer.seq
    assert fn.read_narrative(out)["text"] != "first story"


@pytest.mark.parametrize(
    "earlier, read",
    [
        (("failed", "narrated"), True),
        (("succeeded",), True),
        (("failed",), False),
        (("failed", "succeeded"), False),
    ],
)
def test_an_unnamed_narrative_is_read_by_position_unless_an_earlier_failure_has_none(
    tmp_path, earlier, read
):
    """Ledgers written before narratives named their verdict. The narrative
    after the newest verdict is that verdict's when every failed verdict
    before it has its own (or the attempt before it succeeded); with one
    still unexplained the narrative could be that one's, landing late, so
    the explanation is rendered from the ledger instead."""
    out = tmp_path / ("ws_" + "_".join(earlier))
    ledger = _killed_at_writer(out, "codewhale_worker_failed", "")
    for step in earlier:
        if step == "failed":
            ledger.append(
                EventKind.RUN_FAILED,
                role=BuildRole.WRITER,
                detail="WRITER failed: codewhale_worker_failed",
                payload={"outcome": "FAILED_GATE", "reason": "codewhale_worker_failed"},
            )
        elif step == "succeeded":
            ledger.append(EventKind.RUN_SUCCEEDED, payload={"outcome": "SUCCESS"})
        else:
            fn.attach(ledger, fn.Narrative(text="first story", source=fn.SOURCE_LEDGER, cites=[]))
    _resume(ledger, "WRITER")
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER)
    ledger.append(
        EventKind.GATE_FAILED,
        role=BuildRole.WRITER,
        detail="writer_contract: again",
        payload={"reason": "writer_contract", "location": "WRITER"},
    )
    ledger.append(
        EventKind.RUN_FAILED,
        role=BuildRole.WRITER,
        detail="WRITER failed: writer_contract",
        payload={"outcome": "FAILED_GATE", "reason": "writer_contract"},
    )
    fn.attach(ledger, fn.Narrative(text="second story", source=fn.SOURCE_LEDGER, cites=[]))

    got = fn.read_narrative(out)

    assert (got["text"] == "second story") is read
    assert read or (got["source"] == fn.SOURCE_LEDGER and "writer_contract" in got["text"])


@pytest.mark.parametrize(
    "slug, stored",
    [
        ("blank", {"text": "", "source": "model"}),
        ("no_text", {"source": "model"}),
        ("string", "the run failed"),
        ("list", ["the run failed"]),
        ("none", None),
    ],
)
def test_a_stored_narrative_with_no_text_is_not_the_explanation(tmp_path, slug, stored):
    """A narrative NOTE that names the newest verdict but holds no text (or
    is not a narrative record at all) explains nothing: the run is explained
    from the ledger."""
    out = tmp_path / f"ws_{slug}"
    ledger = _failed_unnarrated(out, "writer_contract")
    ledger.append(
        EventKind.NOTE,
        role=BuildRole.WRITER,
        detail="failure narrative (model)",
        payload={
            fn.NARRATIVE_KEY: stored,
            fn.VERDICT_KEY: fn.newest_verdict_seq(ledger.events()),
        },
    )

    got = fn.read_narrative(out)

    assert got["source"] == fn.SOURCE_LEDGER
    assert "writer_contract" in got["text"]


# ---------------------------------------------------------------------------
# Only a truthy ``resumed`` is a resume (test debt of the 9599cbf grade): a
# NOTE that carries the key with a falsy value closes nothing, and the call
# the attempt left open is still read as this run's overdue call.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "slug, flag, closes",
    [
        ("true", True, True),
        ("word", "yes", True),
        ("false", False, False),
        ("blank", "", False),
        ("none", None, False),
        ("zero", 0, False),
    ],
)
def test_only_a_truthy_resume_flag_closes_the_call_an_attempt_left_open(
    client, tmp_path, live_thread, slug, flag, closes
):
    from app.factory.build_jobs import _open_model_call_note

    out = tmp_path / f"ws_{slug}"
    out.mkdir()
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id="p", inputs_hash="h")
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER)
    _old_open_call(out, "2026-11-27T06:00:00+00:00", 600)
    ledger.append(EventKind.NOTE, detail="a note", payload={"resumed": flag, "phase": "WRITER"})
    sid = _session(f"sess_flag_{slug}", out)
    live_thread(out)
    notes = [e for e in ledger.events() if e.kind is EventKind.NOTE]
    opened = [n for n in notes if (n.payload or {}).get("model_call")]

    got = _open_model_call_note(notes)
    build = client.get(f"/v1/sessions/{sid}/product/build-status").json()["build"]

    assert len(opened) == 1
    assert (got is None) is closes
    assert closes or got is opened[0]
    assert (build["state"] == "building") is closes
    assert ("timed out" in str(build.get("detail") or "")) is (not closes)


# ---------------------------------------------------------------------------
# A workspace with no ledger has no build to read, whatever thread is
# registered for it.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("workspace_exists", [False, True])
def test_a_workspace_with_no_ledger_reads_unknown(tmp_path, live_thread, workspace_exists):
    out = tmp_path / f"ws_{workspace_exists}"
    if workspace_exists:
        out.mkdir()
        live_thread(out)

    status = build_status(out)

    assert status["state"] == "unknown"
    assert "failure" not in status and "phase_trail" not in status


# ---------------------------------------------------------------------------
# Live, 27 Nov: the grader refused the build, the owner pressed Try again, the
# log read "re-entering failed run ... phase WRITER" -- and the page showed no
# change. Public path: start_runner_build over the ledger the runner leaves at
# such a stop. The resume NOTE read here is generate_product's own.
# ---------------------------------------------------------------------------


def _blueprint(product_id: str):
    from app.factory.blueprint import CapabilitySpec, ProductBlueprint

    return ProductBlueprint(
        schema_version="product_blueprint.v1",
        product_id=product_id,
        product_name="P",
        vertical="v",
        summary="s",
        capabilities=[
            CapabilitySpec(
                id="vault", description="vault", block_ids=["storage"], strategy_hint="REUSE"
            )
        ],
    )


def _stopped_as_the_runner_stops(out: Path, stop: str, inputs_hash: str) -> BuildLedger:
    """The ledger and tree a stop leaves, which Try again re-enters in place.

    ``grader_*``: the writer's own gate passed, then the grader refused the
    tree. Its verdict NOTE carries the keys the runner writes and the rows of
    ``grader_findings.finding_rows``; the stop is written by the runner's own
    ``_grader_stop`` (the WRITER's GATE_FAILED), and the run ends at the
    WRITER. ``rule_at_*``: the runner's rule stopped the build at that phase
    (a STOP decision on the run verdict)."""
    from types import SimpleNamespace

    from app.factory.build import rule_decision as rd
    from app.factory.build.grader import ERROR, GRADER_GATE, NOT_CERTIFIED
    from app.factory.build.grader_findings import finding_rows
    from app.factory.build.runner import RoleRunner

    for rel in ("app", "vendor/blocks"):
        (out / rel).mkdir(parents=True, exist_ok=True)
    (out / "blocks.lock.json").write_text("{}", encoding="utf-8")
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id="p", inputs_hash=inputs_hash)
    for role in (BuildRole.COLLECTOR, BuildRole.CLONER):
        ledger.append(EventKind.PHASE_STARTED, role=role)
        ledger.append(EventKind.GATE_PASSED, role=role, payload={"gate": role.value})
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER)
    if stop.startswith("grader"):
        ledger.append(EventKind.GATE_PASSED, role=BuildRole.WRITER, payload={"gate": "WRITER"})
        items = ["the report page shows no totals", "the export button does nothing"]
        verdict = SimpleNamespace(
            tier=ERROR if stop == "grader_error" else NOT_CERTIFIED,
            items=items,
            writer_items=items,
            factory_owed=[],
            grade=None,
        )
        ledger.append(
            EventKind.NOTE,
            role=BuildRole.WRITER,
            detail=f"GRADER: {verdict.tier}: " + "; ".join(items),
            payload={
                "grader_gate": GRADER_GATE,
                "grader_tier": verdict.tier,
                "grader_items": list(items),
                "grader_findings": finding_rows(verdict),
            },
        )
        RoleRunner._grader_stop(SimpleNamespace(ledger=ledger, state={}), verdict, stop)
        ledger.append(
            EventKind.RUN_FAILED,
            role=BuildRole.WRITER,
            detail=f"GRADER: {verdict.tier}",
            payload={"outcome": "FAILED_GATE", "findings": list(items)},
        )
        return ledger
    failed = BuildRole.WRITER
    if stop == "rule_at_tester":
        ledger.append(EventKind.GATE_PASSED, role=BuildRole.WRITER, payload={"gate": "WRITER"})
        ledger.append(EventKind.PHASE_STARTED, role=BuildRole.TESTER)
        failed = BuildRole.TESTER
    ledger.append(
        EventKind.GATE_FAILED,
        role=failed,
        detail="suite_failed: two tests failed",
        payload={"reason": "suite_failed", "location": failed.value},
    )
    ledger.append(
        EventKind.RUN_FAILED,
        role=failed,
        detail=f"{failed.value} failed: suite_failed",
        payload={
            "outcome": "FAILED_GATE",
            "reason": "suite_failed",
            rd.DECISION_KEY: {
                rd.CLASS: rd.STOP,
                rd.GATE: failed.value,
                rd.CHECK: "suite",
                rd.FINDING: "two tests failed",
            },
        },
    )
    return ledger


@pytest.mark.parametrize(
    "stop, shown, phase, step",
    [
        ("grader_not_certified", "grader", "WRITER", 3),
        ("grader_error", "grader", "WRITER", 3),
        ("rule_at_writer", "stopped", "WRITER", 3),
        ("rule_at_tester", "stopped", "TESTER", 4),
    ],
)
def test_try_again_after_a_stop_reads_running_with_none_of_the_stop_left_on_it(
    client, tmp_path, monkeypatch, stop, shown, phase, step
):
    from app.factory.build import coder_session, store_override
    from app.factory.build.runner import blueprint_hash
    from app.factory.build_jobs import start_runner_build

    monkeypatch.setenv("FACTORY_BUILD_ENGINE", "runner")
    bp = _blueprint(f"p-{stop}")
    out = tmp_path / stop / "ws"
    ledger = _stopped_as_the_runner_stops(out, stop, blueprint_hash(bp))
    sid = _session(f"sess_reenter_{stop}", out)
    url = f"/v1/sessions/{sid}/product/build-status"
    monkeypatch.setattr(store_override, "builds_unavailable_reason", lambda _root: None)

    # The stop stands: its own record is on the status.
    before = client.get(url).json()["build"]
    assert before["state"] == "failed"
    assert before[shown]

    # Must not fire: a Try again refused at the door wrote its NOTE and
    # started nothing, so the stop still stands with its record.
    def refuse():
        raise coder_session.CodeCliUnavailable("no coding CLI session")

    monkeypatch.setattr(coder_session, "raise_if_cli_session_unready", refuse)
    with pytest.raises(coder_session.CodeCliUnavailable):
        start_runner_build(bp, out)
    refused = client.get(url).json()["build"]
    assert refused["state"] == "failed"
    assert refused[shown] == before[shown]

    monkeypatch.setattr(coder_session, "raise_if_cli_session_unready", lambda: None)
    held = threading.Event()

    def hold(*_a, **_k):
        held.wait(timeout=10)

    monkeypatch.setattr("app.factory.build_jobs._run", hold)
    try:
        reply = start_runner_build(bp, out)
        waiting = client.get(url).json()["build"]
        ledger.append(EventKind.PHASE_STARTED, role=BuildRole(phase))
        working = client.get(url).json()["build"]
    finally:
        held.set()

    named = [
        (e.payload or {}).get("phase")
        for e in ledger.events()
        if e.kind is EventKind.NOTE and (e.payload or {}).get("resumed")
    ]
    assert named == [phase, phase]
    assert reply["already_running"] is False
    for build in (reply["build"], waiting, working):
        assert build["state"] == "building"
        assert build["failure"] is None
        assert "failed" not in build and "stop" not in build
        assert (build["current_phase"]["id"], build["phase_index"]) == (phase, step)
        # The earlier attempt's grader verdict and rule stop are not this run's.
        assert build["grader"] is None and build["stopped"] is None
