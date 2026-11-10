"""Unit tests for scripts/pilot_runner.py pure stage checks.

No network: each check is a pure function over the live API's JSON shapes.
Two distinct literal cases per assertion so a mutant that flips the verdict is
killed and the two-input lint is satisfied.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
REPO_ROOT = Path(__file__).resolve().parents[3]  # backend/tests/pilot/<file> -> repo root
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

pilot = pytest.importorskip("pilot_runner")

FILES = ["harbourline_contract.pdf", "harbourline_boq.xlsx"]


@pytest.mark.parametrize(
    "result, expected",
    [
        ({"status": "completed", "total_chunks": 12, "indexed_collection": "c1", "failed_files": []}, True),
        ({"status": "completed_with_warnings", "total_chunks": 3, "indexed_collection": "c1", "failed_files": []}, True),
        ({"status": "completed", "total_chunks": 0, "indexed_collection": "c1", "failed_files": []}, False),
        ({"status": "completed", "total_chunks": 9, "indexed_collection": "", "failed_files": []}, False),
        ({"status": "failed", "total_chunks": 9, "indexed_collection": "c1", "failed_files": ["x"]}, False),
    ],
)
def test_upload_indexed(result, expected):
    ok, _ev = pilot.upload_indexed(result, set(FILES))
    assert ok is expected


@pytest.mark.parametrize(
    "product, expected",
    [
        ({"brief": "req from document:harbourline_contract.pdf and document:harbourline_boq.xlsx", "blueprint": {}}, True),
        ({"brief": "", "blueprint": {"notes": "document:harbourline_contract.pdf document:harbourline_boq.xlsx"}}, True),
        ({"brief": "only document:harbourline_contract.pdf", "blueprint": {}}, False),
        ({"brief": "no citations at all", "blueprint": {}}, False),
    ],
)
def test_brief_cites_documents(product, expected):
    ok, _ev = pilot.brief_cites_documents(product, FILES)
    assert ok is expected


@pytest.mark.parametrize(
    "build, zip_ok, expected",
    [
        ({"state": "converged", "phases_done": 5, "phases_total": 5}, True, True),
        ({"state": "complete", "phases_done": 5, "phases_total": 5}, False, False),
        ({"state": "working", "phases_done": 3, "phases_total": 5}, True, False),
        ({"state": "failed", "phases_done": 5, "phases_total": 5}, True, False),
    ],
)
def test_build_reached_store(build, zip_ok, expected):
    ok, _ev = pilot.build_reached_store(build, zip_ok)
    assert ok is expected


_FINDING = "documents were not indexed into the product corpus: retrieval backend down"


@pytest.mark.parametrize(
    "upload, ledger, expected",
    [
        ({"message": "", "failed_files": []}, [], True),
        ({"message": "", "failed_files": []}, [{"payload": {"other": 1}}], True),
        ({"message": _FINDING, "failed_files": [_FINDING]}, [], False),
        ({"message": "", "failed_files": []}, [{"payload": {"corpus_seed_finding": "no tenant"}}], False),
    ],
)
def test_corpus_seed_ok(upload, ledger, expected):
    ok, _ev = pilot.corpus_seed_ok(upload, ledger)
    assert ok is expected


@pytest.mark.parametrize(
    "has_creds, expected",
    [
        (True, True),    # principal present -> OK
        (False, False),  # STRICT: missing principal -> FAIL, never a skip-pass
    ],
)
def test_principal_required_result_is_strict(has_creds, expected):
    ok, _ev = pilot.principal_required_result(has_creds)
    assert ok is expected


@pytest.mark.parametrize(
    "first, second, expected",
    [
        ({"generation": {"output_dir": "/ws/p1"}}, {"generation": {"output_dir": "/ws/p1"}, "already_complete": True}, True),
        ({"generation": {"output_dir": "/ws/p1"}}, {"generation": {"output_dir": "/ws/p1"}, "resumed": True}, True),
        ({"generation": {"output_dir": "/ws/p1"}}, {"generation": {"output_dir": "/ws/p2"}, "resumed": True}, False),
        ({"generation": {"output_dir": "/ws/p1"}}, {"generation": {"output_dir": "/ws/p1"}}, False),
    ],
)
def test_resume_not_restart(first, second, expected):
    ok, _ev = pilot.resume_not_restart(first, second)
    assert ok is expected


@pytest.mark.parametrize(
    "event, expected_line",
    [
        (
            {"ts": "2026-11-08T10:07:00+00:00", "role": "STORE_MANAGER", "kind": "GATE_FAILED", "detail": "zip_http=409", "payload": {"state": "failed"}},
            '2026-11-08T10:07:00+00:00 phase=STORE_MANAGER event=GATE_FAILED zip_http=409 payload={"state": "failed"}',
        ),
        (
            {"ts": "2026-11-08T10:06:50+00:00", "role": "WRITER", "kind": "NOTE", "detail": "model_call", "payload": "short"},
            "2026-11-08T10:06:50+00:00 phase=WRITER event=NOTE model_call payload=short",
        ),
    ],
)
def test_render_ledger_tail_line(event, expected_line):
    # F-G3: one line per event carrying ts, phase (role), event (kind), payload.
    lines = pilot.render_ledger_tail([event])
    assert lines[0] == expected_line


@pytest.mark.parametrize(
    "events, expected",
    [
        ([], ["no ledger events"]),
        (None, ["no ledger events"]),
    ],
)
def test_render_ledger_tail_missing_events_is_explicit(events, expected):
    assert pilot.render_ledger_tail(events) == expected


@pytest.mark.parametrize(
    "payload, expected_len",
    [
        ("x" * 500, 200),
        ("short", 5),
    ],
)
def test_render_ledger_tail_truncates_payload(payload, expected_len):
    lines = pilot.render_ledger_tail([{"ts": "t", "payload": payload}])
    assert len(lines[0].split("payload=", 1)[1]) == expected_len


@pytest.mark.parametrize(
    "product, expect_none",
    [
        ({}, True),
        ({"brief_diag": None}, True),
        ({"brief_diag": {"chunks": 0, "digest_folded": False}}, False),
        ({"brief_diag": {"chunks": 2, "source_files": ["a.pdf", "b.xlsx"], "digest_folded": True}}, False),
    ],
)
def test_brief_diag_summary_reports_presence(product, expect_none):
    out = pilot.brief_diag_summary(product)
    assert (out == "none") == expect_none


@pytest.mark.parametrize(
    "diag, expected_in",
    [
        ({"chunks": 2, "source_files": ["a.pdf"]}, "\"chunks\": 2"),
        ({"digest_folded": False, "worker": "h:1"}, "\"digest_folded\": false"),
    ],
)
def test_brief_diag_summary_quotes_the_fields(diag, expected_in):
    assert expected_in in pilot.brief_diag_summary({"brief_diag": diag})


@pytest.mark.parametrize(
    "event, expected",
    [
        ({"kind": "RUN_FAILED", "detail": "", "payload": {}}, True),
        ({"kind": "PHASE_ABORTED", "detail": "", "payload": {}}, True),
        ({"kind": "GATE_FAILED", "detail": "", "payload": {}}, True),
        ({"kind": "worker_stalled", "detail": "", "payload": {}}, True),
        ({"kind": "NOTE", "detail": "worker_timed_out: wall", "payload": {}}, True),
        ({"kind": "NOTE", "detail": "writer working", "payload": {"stall": "worker_stalled"}}, True),
        ({"kind": "NOTE", "detail": "session finished", "payload": {"model_call": True, "provider": "x"}}, True),
        ({"kind": "NOTE", "detail": "session finished", "payload": {"model_call_state": "closed"}}, True),
        ({"kind": "NOTE", "detail": "session finished", "payload": {"failure_kind": "model_call_timeout"}}, True),
        ({"kind": "NOTE", "detail": "plain note", "payload": {"provider": "x"}}, False),
        ({"kind": "PHASE_STARTED", "detail": "", "payload": {}}, False),
        ({"kind": "NOTE", "detail": "worker count is 2", "payload": {}}, False),
    ],
)
def test_is_reason_event(event, expected):
    # ORDERS_10NOV_1925Z: a reason event is a verdict, or a worker_*/model_call_*
    # marker in kind/detail/typed payload -- never arbitrary prose.
    assert pilot.is_reason_event(event) is expected


@pytest.mark.parametrize(
    "events, expected",
    [
        (
            [
                {"ts": "t1", "kind": "PHASE_STARTED", "detail": "", "payload": {}},
                {"ts": "t2", "kind": "RUN_FAILED", "detail": "boom", "payload": {}},
            ],
            ["t2 phase=- event=RUN_FAILED boom payload={}"],
        ),
        (
            [{"ts": "t1", "kind": "NOTE", "detail": "plain", "payload": {}}],
            ["no RUN_FAILED / PHASE_ABORTED / GATE_FAILED / worker_* / model_call_* event found"],
        ),
    ],
)
def test_render_reason_events_filters(events, expected):
    assert pilot.render_reason_events(events) == expected


@pytest.mark.parametrize(
    "events, expected_lines",
    [
        ([{"seq": 1, "kind": "NOTE"}, {"seq": 2, "kind": "RUN_FAILED"}], 2),
        ([{"seq": 3, "kind": "GATE_FAILED"}], 1),
    ],
)
def test_write_pilot_ledger_writes_jsonl(tmp_path, events, expected_lines):
    ok = pilot.write_pilot_ledger(events, tmp_path / "ledger.jsonl")
    assert ok is True
    raw = (tmp_path / "ledger.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(raw) == expected_lines
