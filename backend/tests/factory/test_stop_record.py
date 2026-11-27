"""One "ended because" record, the allowed actions, and a build that is gone.

A stopped build is described once, from the LAST failure the ledger holds:
its kind (a closed set), its step, the steps done, the fix rounds used and
the time, with the raw message and reference under ``technical``. The page
writes its sentence from those typed fields and draws its buttons from the
actions the server returns for the state.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.factory.build.failure_record as fr
from app.core.auth import DEV_LOCAL_TENANT
from app.core.session_store import create_session, get_session, update_session
from app.factory.build.authority import BuildRole
from app.factory.build.failure_record import fix_rounds_used, reason_chain, stop_kind
from app.factory.build.ledger import BuildLedger, EventKind
from app.factory.build_jobs import (
    _elapsed_s,
    build_outcome,
    build_status,
    failure_records,
    last_failure_event,
    stop_record,
)
from app.factory.floor_actions import allowed_build_actions
from app.main import app
from app.routers.session_product import _level_reaches_pilot, not_found_build_status
from app.routers.sessions import session_build_outcome, session_card

BACKEND = Path(__file__).resolve().parents[2]

#: The actions a STOPPED build offers; a finished build offers none of them.
STOPPED_ACTIONS = {"try_again", "download_copy", "change_answers", "start_over"}


def _ledger(out: Path) -> BuildLedger:
    out.mkdir(parents=True, exist_ok=True)
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id="p", inputs_hash="h")
    for role in (BuildRole.COLLECTOR, BuildRole.CLONER):
        ledger.append(EventKind.PHASE_STARTED, role=role)
        ledger.append(EventKind.GATE_PASSED, role=role, payload={"gate": role.value})
    return ledger


def _contract_send_back(ledger: BuildLedger) -> None:
    """A WRITER gate refusal the runner answered with one fix round."""
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER)
    ledger.append(
        EventKind.GATE_FAILED,
        role=BuildRole.WRITER,
        detail="a country code was typed into the code",
        payload={
            "gate": "writer_contract",
            "reason": "writer_contract",
            "location": "WRITER",
            "decision": {"gate": "WRITER", "class": "REWORK", "check": "c", "finding": "f"},
        },
    )
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER)


@pytest.mark.parametrize(
    "reason, detail, failure_kind",
    [
        # The reason chain names the timeout below the outer worker failure.
        (
            "codewhale_worker_failed",
            "codewhale_worker_failed: worker_timed_out: headless job exceeded its wall",
            "",
        ),
        # The typed kind names it: a timeout by exception type.
        ("writer_role_failed", "the job was killed", "timeout"),
    ],
)
def test_a_timeout_after_a_contract_send_back_is_time_ran_out(tmp_path, reason, detail, failure_kind):
    out = tmp_path / "p"
    ledger = _ledger(out)
    _contract_send_back(ledger)
    ledger.append(
        EventKind.PHASE_ABORTED,
        role=BuildRole.WRITER,
        detail=detail,
        payload={"reason": reason, "location": "WRITER", "failure_kind": failure_kind},
    )
    ledger.append(
        EventKind.RUN_FAILED,
        role=BuildRole.WRITER,
        detail="WRITER could not finish",
        payload={"outcome": "FAILED_ROLE_ERROR"},
    )

    status = build_status(out)
    stop = status["stop"]

    assert status["state"] == "failed"
    assert stop["kind"] == fr.STOP_TIME_RAN_OUT
    assert stop["step"] == "WRITER"
    assert stop["step_index"] == 3
    assert stop["steps_done"] == 2
    assert stop["fix_rounds"] == 1
    # The reference is the LAST failure's, as filed in the build record, and
    # the Floor row quotes that same newest failure, not the first one.
    writer_refs = [r["ref"] for r in failure_records(out) if r["phase"] == "WRITER"]
    assert len(writer_refs) >= 2
    assert stop["technical"]["reference"] == writer_refs[-1]
    assert stop["technical"]["reference"] != writer_refs[0]
    assert stop["technical"]["reference"] == status["failure"]["ref"]
    assert "_" not in stop["plain"]
    assert isinstance(stop["elapsed_s"], int) and stop["elapsed_s"] >= 0


@pytest.mark.parametrize(
    "reason, detail, kind",
    [
        ("codewhale_worker_failed", "codewhale_worker_failed: worker_timed_out: x", fr.STOP_TIME_RAN_OUT),
        ("codewhale_worker_failed", "codewhale_worker_failed: worker_stalled: idle 900s", fr.STOP_NO_PROGRESS),
        ("codewhale_worker_failed", "codewhale_worker_failed: the process exited", fr.STOP_SERVICE_INTERRUPTED),
        ("writer_no_output", "", fr.STOP_NOTHING_PRODUCED),
    ],
)
def test_the_kind_reads_the_whole_reason_chain_innermost_first(reason, detail, kind):
    chain = reason_chain(reason, detail)

    assert chain[0] == reason
    assert stop_kind(chain=chain)[0] == kind


@pytest.mark.parametrize(
    "reason, detail, chain",
    [
        ("a_code", "a_code: b_code: some prose: c_code", ["a_code", "b_code"]),
        ("", "Prose first: a_code", []),
        ("x_code", "", ["x_code"]),
    ],
)
def test_the_chain_stops_at_the_first_piece_of_prose(reason, detail, chain):
    assert reason_chain(reason, detail) == chain


@pytest.mark.parametrize(
    "outcome, markers, gate_failed, kind",
    [
        # The owner's stop and the time wall decide on their own.
        ("STOPPED_BY_OWNER", [], True, fr.STOP_USER_STOPPED),
        ("FAILED_TIME_BUDGET", [], True, fr.STOP_TIME_RAN_OUT),
        # A run that crashed is read from its terminal marker, not from an
        # earlier gate refusal.
        ("", ["build_thread_crashed"], True, fr.STOP_SERVICE_INTERRUPTED),
        # A gate with no entry of its own is a check that did not pass.
        ("FAILED_ROLE_ERROR", [], True, fr.STOP_CHECK_FAILED),
        # No gate refused: the outcome's own entry.
        ("FAILED_ROLE_ERROR", [], False, fr.STOP_NOTHING_PRODUCED),
    ],
)
def test_the_kind_follows_the_order_of_evidence(outcome, markers, gate_failed, kind):
    got = stop_kind(
        outcome=outcome, markers=markers, chain=["some_unlisted_check"], gate_failed=gate_failed
    )

    assert got[0] == kind


@pytest.mark.parametrize(
    "decisions, rounds",
    [
        ([{"class": "REWORK"}, {"class": "ADVISORY"}, {"class": "REPROMPT"}], 2),
        ([{"class": "STOP"}, "not a decision"], 0),
        (None, 0),
    ],
)
def test_fix_rounds_count_only_decisions_that_sent_work_back(decisions, rounds):
    assert fix_rounds_used(decisions) == rounds


# ---------------------------------------------------------------------------
# Every code that can end a run has a plain entry.
# ---------------------------------------------------------------------------


def _module_constants(tree: ast.AST) -> dict:
    out = {}
    for node in getattr(tree, "body", []):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            for target in node.targets:
                if isinstance(target, ast.Name) and isinstance(node.value.value, str):
                    out[target.id] = node.value.value
    return out


def _role_error_reasons() -> set:
    """Every ``RoleError(..., reason=...)`` code under app/factory: the codes a
    phase can abort with. A name is resolved through its module's constants."""
    codes = set()
    for path in (BACKEND / "app" / "factory").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        consts = _module_constants(tree)
        for node in ast.walk(tree):
            func = getattr(node, "func", None)
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if not (isinstance(node, ast.Call) and name == "RoleError"):
                continue
            for kw in node.keywords:
                if kw.arg != "reason":
                    continue
                if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                    codes.add(kw.value.value)
                elif isinstance(kw.value, ast.Name) and kw.value.id in consts:
                    codes.add(consts[kw.value.id])
    return codes


def _outcomes() -> set:
    from app.factory.build.runner import Outcome

    return {o.value for o in Outcome} - {Outcome.SUCCESS.value, Outcome.HANDOFF_TO_N3.value}


def _failure_kinds() -> set:
    from app.factory.build import failure_kinds as fk

    return {fk.TIMEOUT, fk.MODEL_CALL_TIMEOUT, fk.BUDGET_SKIPPED}


def _worker_codes() -> set:
    from app.factory.build import codewhale_worker as cw

    tree = ast.parse(Path(cw.__file__).read_text(encoding="utf-8-sig"))
    codes = {v for v in _module_constants(tree).values() if v.startswith("worker_")}
    return codes | {cw.SLOT_WAIT_EXHAUSTED}


def _status_markers() -> set:
    from app.factory import build_jobs as bj
    from app.factory.build import ledger as lg
    from app.factory.build.runner import STAGE_TIME_CHECK_STOP, STOPPED_BY_OWNER_REASON

    return {
        bj.BUILD_THREAD_ORPHANED,
        bj.FACTORY_CODE_CLI_ORPHANED,
        lg.LEDGER_UNREADABLE,
        lg.LEDGER_EXTERNAL_NOTE_QUARANTINED,
        STOPPED_BY_OWNER_REASON,
        STAGE_TIME_CHECK_STOP,
        "BUILD_THREAD_CRASHED",
        "build_thread_crashed",
        *bj._FAILURE_SUMMARIES,
    }


@pytest.mark.parametrize(
    "source",
    [_outcomes, _failure_kinds, _worker_codes, _role_error_reasons, _status_markers],
    ids=["outcomes", "failure_kinds", "worker_codes", "role_error_reasons", "status_markers"],
)
def test_every_code_that_can_end_a_run_has_a_plain_entry(source):
    codes = source()

    assert codes, "the source found no codes: the scan itself is broken"
    assert sorted(c for c in codes if c not in fr.STOP_ENTRIES) == []


@pytest.mark.parametrize("code", sorted(fr.STOP_ENTRIES))
def test_each_entry_is_a_known_kind_and_a_short_plain_sentence(code):
    kind, plain = fr.STOP_ENTRIES[code]

    assert kind in fr.STOP_KINDS
    assert len(plain.split()) <= 20
    assert "_" not in plain and plain.endswith(".")


# ---------------------------------------------------------------------------
# States without a terminal event, and the last failure.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status, kind",
    [
        ({"state": "stalled", "honesty": "BUILD_THREAD_ORPHANED"}, fr.STOP_SERVICE_INTERRUPTED),
        ({"state": "stalled"}, fr.STOP_NO_PROGRESS),
        ({"state": "failed", "honesty": "BUILD_THREAD_CRASHED"}, fr.STOP_SERVICE_INTERRUPTED),
        ({"state": "failed"}, fr.STOP_TIME_RAN_OUT),
    ],
)
def test_a_run_with_no_terminal_event_is_read_from_its_marker(tmp_path, status, kind):
    out = tmp_path / "p"
    ledger = _ledger(out)
    _contract_send_back(ledger)  # an earlier, recovered refusal is not the end

    stop = stop_record({**status, "current_phase": {"id": "WRITER"}}, out)

    assert stop["kind"] == kind
    assert stop["step"] == "WRITER"


@pytest.mark.parametrize("state", ["succeeded", "building", "unknown"])
def test_only_a_build_that_ended_early_has_a_stop_record(tmp_path, state):
    out = tmp_path / "p"
    _ledger(out)

    assert stop_record({"state": state}, out) is None


@pytest.mark.parametrize(
    "handed_off, reason",
    [(True, None), (False, "docker_unavailable")],
)
def test_a_designed_handoff_is_not_the_last_failure(tmp_path, handed_off, reason):
    ledger = _ledger(tmp_path / "p")
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.STORE_MANAGER)
    ledger.append(
        EventKind.GATE_FAILED,
        role=BuildRole.STORE_MANAGER,
        payload={"gate": "store", "reason": "docker_unavailable"},
    )
    if handed_off:
        ledger.append(EventKind.NOTE, role=BuildRole.STORE_MANAGER, payload={"handoff": True})

    last = last_failure_event(ledger.events())

    assert (None if last is None else last.payload["reason"]) == reason


# ---------------------------------------------------------------------------
# Allowed actions.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "build, reaches_pilot, actions",
    [
        ({"state": "succeeded", "pilot_ready": False}, True, ["download_platform", "run_pilot"]),
        ({"state": "succeeded", "pilot_ready": False}, False, ["download_platform"]),
        (
            {"state": "succeeded", "pilot_ready": True, "repo_url": "u", "delivery_format": "github_repo"},
            True,
            ["open_repo", "download_platform"],
        ),
        ({"state": "building"}, None, ["stop"]),
        ({"state": "building", "n3_waiting": True}, None, ["try_again"]),
        ({"state": "stalled"}, None, ["try_again", "download_copy", "start_over"]),
        (
            {"state": "failed", "stop": {"kind": "time_ran_out"}},
            None,
            ["try_again", "download_copy", "change_answers", "start_over"],
        ),
        (
            {"state": "failed", "stop": {"kind": "check_failed"}},
            None,
            ["try_again", "change_answers", "download_copy", "start_over"],
        ),
        (
            {"state": "failed", "stop": {"kind": "user_stopped"}},
            None,
            ["try_again", "download_copy", "change_answers", "start_over"],
        ),
        ({"state": "not_found"}, None, ["check_again", "start_over"]),
        ({"state": "not_started"}, None, []),
        (None, None, []),
    ],
)
def test_the_actions_follow_the_state(build, reaches_pilot, actions):
    assert allowed_build_actions(build, reaches_pilot=reaches_pilot) == actions


@pytest.mark.parametrize("pilot_ready", [True, False])
def test_a_succeeded_build_offers_no_stopped_build_action(tmp_path, pilot_ready):
    out = tmp_path / "p"
    ledger = _ledger(out)
    _contract_send_back(ledger)  # recovered: history, not the outcome
    for role in (BuildRole.WRITER, BuildRole.TESTER, BuildRole.STORE_MANAGER):
        ledger.append(EventKind.GATE_PASSED, role=role, payload={"gate": role.value})
    ledger.append(EventKind.RUN_SUCCEEDED, payload={"outcome": "SUCCESS", "pilot_ready": pilot_ready})

    status = build_status(out)
    actions = allowed_build_actions(status, reaches_pilot=True)

    assert status["state"] == "succeeded"
    assert "stop" not in status
    assert not STOPPED_ACTIONS & set(actions)
    assert actions[0] == "download_platform"


# ---------------------------------------------------------------------------
# The build-status route and the session list.
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("ENV", "test")
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)
    monkeypatch.setenv("ALLOW_ANONYMOUS_DEV", "1")
    return TestClient(app)


@pytest.mark.parametrize(
    "workspace, state, actions",
    [
        ("missing", "not_found", ["check_again", "start_over"]),
        (None, "not_started", []),
    ],
)
def test_the_route_names_a_missing_workspace_and_its_actions(client, tmp_path, workspace, state, actions):
    """get_build_status: a missing workspace is "not_found", never "unknown"."""
    sid = f"sess_stop_{state}"
    create_session(sid, DEV_LOCAL_TENANT)
    if workspace is not None:
        session = get_session(sid)
        session.product_design.generation = {"product_id": "p", "output_dir": str(tmp_path / workspace)}
        update_session(sid, session)

    body = client.get(f"/v1/sessions/{sid}/product/build-status").json()

    assert body["build"]["state"] == state
    assert body["actions"] == actions
    assert (body["build"].get("stop") or {}).get("kind") == (fr.STOP_NOT_FOUND if workspace else None)


@pytest.mark.parametrize(
    "terminal, outcome",
    [
        (EventKind.RUN_SUCCEEDED, "ready"),
        (EventKind.RUN_FAILED, "stopped"),
        (None, "building"),
    ],
)
def test_build_outcome_reads_the_terminal_event(tmp_path, terminal, outcome):
    out = tmp_path / "p"
    ledger = _ledger(out)
    if terminal is not None:
        ledger.append(terminal, payload={"outcome": "X"})

    assert build_outcome(out) == outcome


@pytest.mark.parametrize(
    "generation, outcome",
    [
        ({"product_id": "p", "output_dir": "MISSING"}, "not_found"),
        ({"product_id": "p"}, None),
        (None, None),
    ],
)
def test_the_session_card_carries_the_build_outcome(tmp_path, generation, outcome):
    from app.models.session import ProductDesignState, SessionState

    state = SessionState(session_id="sess-outcome", user_id="u1", account_id="a1")
    if generation and generation.get("output_dir"):
        generation = {**generation, "output_dir": str(tmp_path / generation["output_dir"])}
    state.product_design = ProductDesignState(generation=generation)

    assert session_card(state)["outcome"] == outcome


@pytest.mark.parametrize(
    "blueprint, said, title",
    [
        ({}, "a booking tool for my clinics", "a booking tool for my clinics"),
        ({"product_name": "Clinic Desk"}, "a booking tool for my clinics", "Clinic Desk"),
    ],
)
def test_the_card_title_falls_back_to_what_the_user_said_only_before_a_draft(blueprint, said, title):
    """The card that carries the outcome is still found by its title: the
    product name once drafted, otherwise the user's first real line."""
    from app.models.session import ProductDesignState, SessionState

    state = SessionState(session_id="sess-title", user_id="u1", account_id="a1")
    state.product_design = ProductDesignState(blueprint=blueprint)
    state.chat_history = [{"role": "user", "content": "hi"}, {"role": "user", "content": said}]

    assert session_card(state)["title"] == title


@pytest.mark.parametrize(
    "stamps, elapsed",
    [
        ([], None),
        (["2026-11-21T10:00:00Z"], None),
        (["2026-11-21T10:00:00Z", "not a time", "2026-11-21T10:38:30Z"], 2310),
    ],
)
def test_elapsed_time_runs_from_the_first_event_to_the_last(stamps, elapsed):
    assert _elapsed_s([SimpleNamespace(ts=s) for s in stamps]) == elapsed


def _event(kind, role, reason):
    return SimpleNamespace(kind=kind, role=role, payload={"reason": reason}, detail="", ts="")


@pytest.mark.parametrize(
    "events, reason",
    [
        # A refusal with no step is not a step's failure.
        ([_event("GATE_FAILED", "WRITER", "first"), _event("GATE_FAILED", None, "stepless")], "first"),
        ([_event("GATE_FAILED", "WRITER", "first"), _event("PHASE_ABORTED", "TESTER", "second")], "second"),
    ],
)
def test_the_last_failure_belongs_to_a_step(events, reason):
    assert last_failure_event(events).payload["reason"] == reason


@pytest.mark.parametrize(
    "level, reaches",
    [("prototype", False), ("pilot", True), ("", None), ("no-such-level", None)],
)
def test_the_session_level_says_whether_pilot_checks_are_offered(level, reaches):
    assert _level_reaches_pilot(SimpleNamespace(build_level=level)) is reaches


@pytest.mark.parametrize("field", ["kind", "plain", "check_plain", "restarted"])
def test_the_not_found_state_carries_its_own_stop_record(field):
    """A build the server cannot find names no check and no restart."""
    build = not_found_build_status()
    expected = {
        "kind": fr.STOP_NOT_FOUND,
        "plain": fr.NOT_FOUND_PLAIN,
        "check_plain": None,
        "restarted": False,
    }[field]

    assert build["state"] == "not_found"
    assert build["stop"][field] == expected
    assert allowed_build_actions(build) == ["check_again", "start_over"]


@pytest.mark.parametrize(
    "generation, outcome",
    [({"product_id": "p", "output_dir": "MISSING"}, "not_found"), ({}, None)],
)
def test_session_build_outcome_reads_only_a_started_build(tmp_path, generation, outcome):
    if generation:
        generation = {**generation, "output_dir": str(tmp_path / generation["output_dir"])}
    state = SimpleNamespace(product_design=SimpleNamespace(generation=generation))

    assert session_build_outcome(state) == outcome
