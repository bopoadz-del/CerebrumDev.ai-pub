"""A two-hour build must survive an interrupt and be honest afterwards.

New-shape tests for the manufacturing ledger. The failures these guard
against are the ones that make a long run untrustworthy rather than broken:
a resume that silently continues against a changed blueprint, a phase that
counts as done because it passed once before being aborted, and a run that
converged after eleven writer/tester round trips but reads like it passed
first try.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app.factory.build.authority import BuildRole
from app.factory.build.ledger import (
    BuildLedger,
    EventKind,
    LedgerError,
    iter_ledgers,
)


@pytest.fixture()
def ledger(tmp_path: Path) -> BuildLedger:
    counter = {"n": 0}

    def clock() -> str:
        counter["n"] += 1
        return f"2026-08-11T00:00:{counter['n']:02d}+00:00"

    return BuildLedger(tmp_path / "run" / "build_ledger.jsonl", clock=clock)


def _pass(ledger: BuildLedger, *roles: BuildRole) -> None:
    for role in roles:
        ledger.append(EventKind.PHASE_STARTED, role=role)
        ledger.append(EventKind.GATE_PASSED, role=role)


def test_ledger_is_created_on_first_append_and_is_append_only(ledger):
    assert not ledger.exists()
    ledger.start_run(product_id="retail_ops", inputs_hash="abc123def456")
    ledger.append(EventKind.NOTE, detail="second")

    lines = ledger.path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert [json.loads(x)["seq"] for x in lines] == [1, 2]
    # The first line is untouched by the second write.
    assert json.loads(lines[0])["payload"]["product_id"] == "retail_ops"


def test_resume_point_walks_the_phase_order(ledger):
    ledger.start_run(product_id="p", inputs_hash="h")
    assert ledger.resume_point() is BuildRole.COLLECTOR

    _pass(ledger, BuildRole.COLLECTOR, BuildRole.CLONER)
    assert ledger.resume_point() is BuildRole.WRITER
    assert ledger.completed_roles() == {BuildRole.COLLECTOR, BuildRole.CLONER}


def test_run_is_complete_only_when_every_phase_passed(ledger):
    _pass(
        ledger,
        BuildRole.COLLECTOR,
        BuildRole.CLONER,
        BuildRole.WRITER,
        BuildRole.TESTER,
    )
    assert ledger.resume_point() is BuildRole.STORE_MANAGER
    assert ledger.summary()["complete"] is False

    _pass(ledger, BuildRole.STORE_MANAGER)
    assert ledger.resume_point() is None
    assert ledger.summary()["complete"] is True


def test_a_failed_phase_does_not_count_as_done(ledger):
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.COLLECTOR)
    ledger.append(EventKind.GATE_FAILED, role=BuildRole.COLLECTOR, detail="block missing")
    assert ledger.completed_roles() == set()
    assert ledger.resume_point() is BuildRole.COLLECTOR


def test_latest_verdict_wins_in_both_directions(ledger):
    """Retry-then-pass counts; pass-then-abort does not."""
    ledger.append(EventKind.GATE_FAILED, role=BuildRole.WRITER)
    ledger.append(EventKind.GATE_PASSED, role=BuildRole.WRITER)
    assert BuildRole.WRITER in ledger.completed_roles()

    ledger.append(EventKind.GATE_PASSED, role=BuildRole.TESTER)
    ledger.append(EventKind.PHASE_ABORTED, role=BuildRole.TESTER, detail="interrupted")
    assert BuildRole.TESTER not in ledger.completed_roles()


def test_resume_against_changed_inputs_is_refused(ledger):
    ledger.start_run(product_id="p", inputs_hash="aaaaaaaaaaaa")
    ledger.assert_resumable(inputs_hash="aaaaaaaaaaaa")

    with pytest.raises(LedgerError, match="cannot resume"):
        ledger.assert_resumable(inputs_hash="bbbbbbbbbbbb")


def test_fresh_ledger_resumes_against_anything(ledger):
    """No RUN_STARTED yet means nothing to contradict."""
    ledger.assert_resumable(inputs_hash="whatever")


def test_rework_rounds_are_visible_in_the_summary(ledger):
    for _ in range(3):
        ledger.append(EventKind.REWORK, role=BuildRole.WRITER, detail="tests red")
    assert ledger.rework_counts() == {"WRITER": 3}
    assert ledger.summary()["rework"] == {"WRITER": 3}


def test_clones_are_registered_for_the_store_registrar(ledger):
    ledger.record_clone(
        block_id="invoice_parser",
        source_commit="0123456789abcdef",
        store_repo="Cerebrum-Blocks",
        vendored_path="vendor/blocks/invoice_parser",
    )
    ledger.record_clone(
        block_id="web",
        source_commit="fedcba9876543210",
        store_repo="Cerebrum-Blocks",
        vendored_path="vendor/blocks/web",
    )

    clones = ledger.clones()
    assert [c["block_id"] for c in clones] == ["invoice_parser", "web"]
    assert clones[0]["source_commit"] == "0123456789abcdef"


def test_re_cloning_a_block_reports_the_latest_commit_only(ledger):
    """A rebuilt platform has one current version per block, not a history."""
    ledger.record_clone(
        block_id="web", source_commit="a" * 16, store_repo="S", vendored_path="v/web"
    )
    ledger.record_clone(
        block_id="web", source_commit="b" * 16, store_repo="S", vendored_path="v/web"
    )

    clones = ledger.clones()
    assert len(clones) == 1
    assert clones[0]["source_commit"] == "b" * 16


def test_corrupt_ledger_raises_rather_than_silently_losing_history(tmp_path):
    """Mid-file non-JSON is real corruption, not a crash-truncated tail."""
    path = tmp_path / "build_ledger.jsonl"
    path.write_text(
        '{"seq": 1, "kind": "NOTE"}\nnot json at all\n{"seq": 2, "kind": "NOTE"}\n',
        encoding="utf-8",
    )

    with pytest.raises(LedgerError, match="readable ledger event"):
        BuildLedger(path).events()


def test_half_written_final_line_is_skipped_not_a_brick(tmp_path):
    """A crash mid-append leaves a truncated last line. Resume must continue."""
    path = tmp_path / "build_ledger.jsonl"
    path.write_text('{"seq": 1, "kind": "NOTE"}\n{"seq": 2, "kin', encoding="utf-8")

    events = BuildLedger(path).events()
    assert [e.seq for e in events] == [1]
    nxt = BuildLedger(path).append(EventKind.NOTE, detail="recovered")
    assert nxt.seq == 2


# Live CEREBRUMDEV-BACKEND-B (sess_69f28c0d8bc540e9 / veterinary-care:4561).
# FACTORY_CODE_CLI wrote a complete NOTE as raw JSONL: microsecond ts, unsorted
# keys, payload.source=coder CLI, no seq. events() KeyError('seq') then bricked
# every later append / chat / status read.
_SENTRY_SEQLESS_NOTE = {
    "ts": "2026-09-05T09:54:45.681451+00:00",
    "role": "WRITER",
    "kind": "NOTE",
    "detail": (
        "rework fix: workflow steps now carry params.action (workflow block "
        "forwards only step['params'] to children; step['action'] alone reached "
        "event_bus as action=None). Patched app/block_inputs.py."
    ),
    "payload": {
        "source": "coder CLI",
        "stage": "rework",
        "files": ["app/block_inputs.py"],
    },
}


def _write_sentry_shaped_ledger(path: Path) -> None:
    factory = {
        "seq": 1,
        "ts": "2026-09-05T09:10:55+00:00",
        "kind": "RUN_STARTED",
        "role": None,
        "detail": "build of veterinary-care",
        "payload": {
            "schema": "build_ledger.v1",
            "product_id": "veterinary-care",
            "inputs_hash": "abc123",
        },
    }
    phase = {
        "seq": 2,
        "ts": "2026-09-05T09:54:40+00:00",
        "kind": "PHASE_STARTED",
        "role": "WRITER",
        "detail": "WRITER",
        "payload": {},
    }
    path.write_text(
        json.dumps(factory, sort_keys=True)
        + "\n"
        + json.dumps(phase, sort_keys=True)
        + "\n"
        + json.dumps(_SENTRY_SEQLESS_NOTE)
        + "\n",
        encoding="utf-8",
    )


def test_mutation_sentry_seqless_note_does_not_brick_append(tmp_path):
    """Kills: events() / _next_seq KeyError('seq') on a complete external NOTE."""
    from app.factory.build.ledger import LEDGER_EXTERNAL_NOTE_QUARANTINED

    path = tmp_path / "build_ledger.jsonl"
    _write_sentry_shaped_ledger(path)
    ledger = BuildLedger(path)

    events = ledger.events()
    assert len(events) == 3
    note = events[-1]
    assert note.kind is EventKind.NOTE
    assert note.role is BuildRole.WRITER
    assert note.seq == 0
    assert note.payload["source"] == "coder CLI"
    assert note.payload["stage"] == "rework"
    assert note.payload["ledger_quarantine"] == LEDGER_EXTERNAL_NOTE_QUARANTINED
    assert ledger.quarantined_notes() == 1
    assert ledger.summary()["quarantined_notes"] == 1

    passed = ledger.append(
        EventKind.GATE_PASSED,
        role=BuildRole.WRITER,
        detail="writer gate",
    )
    assert passed.seq == 3
    assert BuildRole.WRITER in ledger.completed_roles()
    assert ledger.pilot_ready() is False


def test_mutation_seqless_verdict_still_raises(tmp_path):
    """Kills: treating a seq-less GATE_PASSED as a quarantined NOTE."""
    path = tmp_path / "build_ledger.jsonl"
    path.write_text(
        json.dumps({"seq": 1, "kind": "NOTE"})
        + "\n"
        + json.dumps(
            {
                "ts": "2026-09-05T09:54:45+00:00",
                "role": "WRITER",
                "kind": "GATE_PASSED",
                "detail": "forged",
                "payload": {},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(LedgerError, match="readable ledger event: 'seq'"):
        BuildLedger(path).events()


def test_mutation_protect_blocks_raw_cli_scribble(ledger):
    """Kills: FACTORY_CODE_CLI appending raw JSONL while the role is running."""
    ledger.start_run(product_id="veterinary-care", inputs_hash="h")
    with ledger.protect():
        mode = ledger.path.stat().st_mode
        assert mode & 0o222 == 0
        with pytest.raises(PermissionError):
            with ledger.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(_SENTRY_SEQLESS_NOTE) + "\n")
        via_api = ledger.append(
            EventKind.NOTE,
            role=BuildRole.WRITER,
            detail="factory note during protect",
            payload={"source": "coder CLI", "stage": "dispatch"},
        )
        assert via_api.seq == 2
        assert "seq" in json.loads(ledger.path.read_text(encoding="utf-8").splitlines()[-1])
    # Unprotected again so later factory writes and tests can rewrite.
    assert ledger.path.stat().st_mode & 0o200


def test_append_after_killed_protect_leftover_readonly(tmp_path):
    """Process restart leaves 0444 from protect(); a new instance must still append.

    Live 2026-09-07 ~05:08Z: orphan_recovery could not note resume because
    build_ledger.jsonl under the Render disk stayed owner-readonly after
    the previous WRITER protect() never reached finally.
    """
    path = tmp_path / "estate-management" / "build_ledger.jsonl"
    path.parent.mkdir(parents=True)
    first = BuildLedger(path)
    first.start_run(product_id="estate-management", inputs_hash="h")
    os.chmod(path, 0o444)
    assert path.stat().st_mode & 0o222 == 0

    restarted = BuildLedger(path)
    event = restarted.append(EventKind.NOTE, detail="boot note after restart")
    assert event.seq == 2
    assert "boot note after restart" in path.read_text(encoding="utf-8")


def test_mutation_build_status_survives_sentry_note(tmp_path):
    """Kills: chat/status returning 'ledger unreadable' for a seq-less NOTE.

    And kills: Floor/Platforms still claiming Building / taken-over after
    the thread died (sess_69f28c0d8bc540e9 ~09:55Z).
    """
    from app.factory.build.ledger import LEDGER_EXTERNAL_NOTE_QUARANTINED
    from app.factory.build_jobs import build_status

    out = tmp_path / "veterinary-care"
    out.mkdir()
    _write_sentry_shaped_ledger(out / "build_ledger.jsonl")

    status = build_status(out)
    assert "ledger unreadable" not in str(status.get("detail") or "")
    assert status.get("ledger_quarantined_notes") == 1
    assert status["state"] == "stalled"
    assert status.get("honesty") == LEDGER_EXTERNAL_NOTE_QUARANTINED
    assert status.get("pilot_ready") is False
    assert "not still coding" in str(status.get("detail") or "")


def test_mutation_unreadable_ledger_is_failed_not_unknown(tmp_path):
    """Kills: state=unknown on a torn ledger so Floor keeps 'taken over'."""
    from app.factory.build.ledger import LEDGER_UNREADABLE
    from app.factory.build_jobs import build_status

    out = tmp_path / "veterinary-care"
    out.mkdir()
    (out / "build_ledger.jsonl").write_text(
        '{"seq": 1, "kind": "NOTE"}\nnot json at all\n{"seq": 2, "kind": "NOTE"}\n',
        encoding="utf-8",
    )
    status = build_status(out)
    assert status["state"] == "failed"
    assert status.get("honesty") == LEDGER_UNREADABLE
    assert LEDGER_UNREADABLE in str(status.get("detail") or "")
    assert status.get("pilot_ready") is False


def test_mutation_crash_marker_is_failed_when_run_failed_could_not_append(tmp_path):
    """Kills: crash handler failing to append, Floor still Building."""
    from app.factory.build_jobs import CRASH_MARKER_NAME, build_status

    out = tmp_path / "veterinary-care"
    out.mkdir()
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id="veterinary-care", inputs_hash="h")
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER)
    (out / CRASH_MARKER_NAME).write_text("build thread crashed\n", encoding="utf-8")

    status = build_status(out)
    assert status["state"] == "failed"
    assert status.get("honesty") == "BUILD_THREAD_CRASHED"
    assert status.get("pilot_ready") is False


def test_mutation_crash_append_works_after_sentry_note(tmp_path):
    """Kills: crash handler KeyError('seq') so RUN_FAILED never lands."""
    path = tmp_path / "build_ledger.jsonl"
    _write_sentry_shaped_ledger(path)
    crashed = BuildLedger(path).append(
        EventKind.RUN_FAILED,
        detail="build thread crashed; see service logs",
    )
    assert crashed.seq == 3
    assert BuildLedger(path).terminal_event() is not None
    assert BuildLedger(path).terminal_event().kind is EventKind.RUN_FAILED


def test_registrar_can_scan_every_platform_ledger(tmp_path):
    for name in ("alpha", "beta"):
        led = BuildLedger(tmp_path / name / "build_ledger.jsonl")
        led.start_run(product_id=name, inputs_hash=name * 4)
        led.record_clone(
            block_id="web", source_commit="c" * 16, store_repo="S", vendored_path="v/web"
        )

    found = {led.summary()["inputs_hash"]: led.clones() for led in iter_ledgers(tmp_path)}
    assert set(found) == {"alpha" * 4, "beta" * 4}
    assert all(c[0]["block_id"] == "web" for c in found.values())


def test_scanning_a_missing_root_is_empty_not_an_error(tmp_path):
    assert list(iter_ledgers(tmp_path / "nope")) == []


def test_pilot_cycle_reopens_tester_and_store_without_wiping_writer(ledger):
    ledger.start_run(product_id="used-cars", inputs_hash="h")
    _pass(
        ledger,
        BuildRole.COLLECTOR,
        BuildRole.CLONER,
        BuildRole.WRITER,
        BuildRole.TESTER,
        BuildRole.STORE_MANAGER,
    )
    ledger.append(EventKind.RUN_SUCCEEDED, detail="all phase gates passed")
    assert ledger.succeeded() is True
    assert ledger.pilot_ready() is False
    assert ledger.resume_point() is None

    ledger.open_pilot_cycle()
    assert ledger.succeeded() is False
    assert ledger.pilot_cycle_open() is True
    assert ledger.code_phase_succeeded() is True
    done = ledger.completed_roles()
    assert BuildRole.WRITER in done
    assert BuildRole.CLONER in done
    assert BuildRole.TESTER not in done
    assert BuildRole.STORE_MANAGER not in done
    assert ledger.resume_point() is BuildRole.TESTER


def test_pilot_cycle_can_reopen_writer_when_acceptance_surface_is_missing(ledger):
    ledger.start_run(product_id="used-cars", inputs_hash="h")
    _pass(
        ledger,
        BuildRole.COLLECTOR,
        BuildRole.CLONER,
        BuildRole.WRITER,
        BuildRole.TESTER,
        BuildRole.STORE_MANAGER,
    )
    ledger.append(
        EventKind.RUN_SUCCEEDED,
        detail="all phase gates passed",
        payload={"cycle": "pilot", "pilot_ready": True},
    )
    assert ledger.pilot_ready() is True
    assert ledger.resume_point() is None

    ledger.open_pilot_cycle(
        reason="acceptance not k/k; re-opening STORE measurement",
        reopen_writer=True,
    )
    done = ledger.completed_roles()
    assert BuildRole.WRITER not in done
    assert BuildRole.TESTER not in done
    assert BuildRole.STORE_MANAGER not in done
    assert BuildRole.CLONER in done
    assert ledger.resume_point() is BuildRole.WRITER
    assert ledger.pilot_ready() is False

    _pass(ledger, BuildRole.TESTER, BuildRole.STORE_MANAGER)
    ledger.append(
        EventKind.RUN_SUCCEEDED,
        detail="all phase gates passed",
        payload={"cycle": "pilot", "pilot_ready": True},
    )
    assert ledger.succeeded() is True
    assert ledger.pilot_ready() is True
    assert ledger.pilot_cycle_open() is False
