"""Unit tests for scripts/pilot_runner.py pure stage checks.

No network: each check is a pure function over the live API's JSON shapes.
Two distinct literal cases per assertion so a mutant that flips the verdict is
killed and the two-input lint is satisfied.
"""
from __future__ import annotations

import json
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
            {
                "ts": "2026-11-08T10:07:00+00:00",
                "role": "STORE_MANAGER",
                "kind": "GATE_FAILED",
                "detail": "zip_http=409",
                "payload": {"state": "failed", "files": 0, "steps": 0},
            },
            "2026-11-08T10:07:00+00:00 phase=STORE_MANAGER event=GATE_FAILED "
            "failure_kind=GATE_FAILED zip_http=409 counts={files=0,steps=0}",
        ),
        (
            {
                "ts": "2026-11-08T10:06:50+00:00",
                "role": "WRITER",
                "kind": "NOTE",
                "detail": "writer working",
                "payload": {"files_on_disk": 44, "steps_reported": 0, "stall_idle_s": 240.5},
            },
            "2026-11-08T10:06:50+00:00 phase=WRITER event=NOTE "
            "failure_kind=- writer working counts={files=44,idle_s=240.5,steps=0}",
        ),
    ],
)
def test_render_timeline_line(event, expected_line):
    # ORDERS_10NOV_1940Z: one redacted line per event -- ts, phase, event,
    # failure class/kind, reason and counts; never the payload.
    lines = pilot.render_timeline([event])
    assert lines[0] == expected_line
    assert "payload" not in lines[0]


@pytest.mark.parametrize(
    "events, expected",
    [
        ([], ["no ledger events"]),
        (None, ["no ledger events"]),
    ],
)
def test_render_timeline_missing_events_is_explicit(events, expected):
    assert pilot.render_timeline(events) == expected


@pytest.mark.parametrize(
    "event, forbidden",
    [
        (
            {
                "ts": "t",
                "role": "WRITER",
                "kind": "NOTE",
                "detail": "ok",
                "payload": {
                    "brief": "SECRETBRIEF",
                    "prompt": "SECRETPROMPT",
                    "path": "/ws/secret",
                    "session_id": "sid-123",
                    "token": "tok-123",
                    "steps_reported": 3,
                },
            },
            {"SECRETBRIEF", "SECRETPROMPT", "/ws/secret", "sid-123", "tok-123", "payload"},
        ),
        (
            {
                "ts": "t",
                "kind": "RUN_FAILED",
                "detail": "boom",
                "payload": {"decision": {"findings": ["doc-name.pdf"]}, "files": 2},
            },
            {"decision", "findings", "doc-name.pdf", "payload"},
        ),
    ],
)
def test_redact_event_drops_payload_and_secrets(event, forbidden):
    # ORDERS_10NOV_1940Z: payloads, briefs, prompts, doc names, paths, session
    # ids and tokens never reach the printed/uploaded timeline.
    red = pilot.redact_event(event)
    flat = json.dumps(red, ensure_ascii=False, default=str)
    for word in forbidden:
        assert word not in flat


@pytest.mark.parametrize(
    "event, expected",
    [
        ({"kind": "RUN_FAILED", "detail": "", "payload": {}}, "RUN_FAILED"),
        ({"kind": "NOTE", "detail": "", "payload": {"failure_kind": "timeout"}}, "timeout"),
        ({"kind": "NOTE", "detail": "", "payload": {"stall": "worker_stalled"}}, "worker_stalled"),
        ({"kind": "NOTE", "detail": "model_call_timeout", "payload": {}}, "model_call_timeout"),
        ({"kind": "worker_stalled", "detail": "", "payload": {}}, "worker_stalled"),
        ({"kind": "NOTE", "detail": "plain", "payload": {"other": 1}}, ""),
        ({"kind": "PHASE_STARTED", "detail": "", "payload": {}}, ""),
    ],
)
def test_failure_kind_of(event, expected):
    assert pilot.failure_kind_of(event) == expected


@pytest.mark.parametrize(
    "event, expected",
    [
        (
            {"payload": {"files_on_disk": 44, "steps_reported": 0, "stall_idle_s": 240.5}},
            {"files": 44, "steps": 0, "idle_s": 240.5},
        ),
        (
            {"payload": {"stall_files": 9, "stall_steps": 2, "stall_idle_s": 30}},
            {"files": 9, "steps": 2, "idle_s": 30.0},
        ),
        ({"payload": {"since_s": 11.2}}, {"idle_s": 11.2}),
        ({"payload": {"files": 5, "steps": 1, "since_s": 3}}, {"files": 5, "steps": 1, "idle_s": 3.0}),
        ({"payload": {"other": "x"}}, {}),
        ({"payload": "not a dict"}, {}),
    ],
)
def test_counts_of_keeps_only_named_counts(event, expected):
    assert pilot.counts_of(event) == expected


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
            ["t2 phase=- event=RUN_FAILED failure_kind=RUN_FAILED boom"],
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
        (
            [
                {"seq": 1, "kind": "NOTE", "payload": {"secret": "x", "steps": 2}},
                {"seq": 2, "kind": "RUN_FAILED", "detail": "boom", "payload": {"prompt": "y"}},
            ],
            2,
        ),
        ([{"seq": 3, "kind": "GATE_FAILED", "payload": {"session_id": "z"}}], 1),
    ],
)
def test_write_pilot_timeline_writes_jsonl(tmp_path, events, expected_lines):
    ok = pilot.write_pilot_timeline(events, tmp_path / "timeline.jsonl")
    assert ok is True
    raw = (tmp_path / "timeline.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(raw) == expected_lines
    # Redaction: the full payload never lands in the uploaded artifact.
    joined = "\n".join(raw)
    for word in ("secret", "prompt", "session_id", "payload"):
        assert word not in joined


@pytest.mark.parametrize(
    "reasons, expected_pairs, expected_counts",
    [
        (
            [
                {"capability": "zorblat_sum", "class": "stub_handler", "layer": "function"},
                {"capability": "plinket_rate", "class": "stub_handler", "layer": "function"},
                {"capability": "-", "class": "layer2_fail", "layer": "layer2", "detail": "SECRETPROSE"},
                {"capability": "/ws/secret path", "class": "missing_route"},
                {"capability": "quarm_index", "class": "Writer said SECRETPROSE"},
                "SECRETSTRING",
            ],
            [
                {"capability": "zorblat_sum", "class": "stub_handler"},
                {"capability": "plinket_rate", "class": "stub_handler"},
                {"capability": "-", "class": "layer2_fail"},
            ],
            {"stub_handler": 2, "layer2_fail": 1},
        ),
        (
            [
                {"capability": "glim_score", "class": "handler_error"},
                {"capability": "glim_score", "class": "wrong_envelope"},
                {"capability": "trell_mean", "class": "missing_route"},
                {"capability": "trell_mean", "class": "missing_route"},
            ],
            [
                {"capability": "glim_score", "class": "handler_error"},
                {"capability": "glim_score", "class": "wrong_envelope"},
                {"capability": "trell_mean", "class": "missing_route"},
                {"capability": "trell_mean", "class": "missing_route"},
            ],
            {"handler_error": 1, "wrong_envelope": 1, "missing_route": 2},
        ),
    ],
    ids=["mix_with_junk", "clean_mix"],
)
def test_grader_reasons_pass_through_by_shape_and_are_counted(tmp_path, reasons, expected_pairs, expected_counts):
    # Option (b): the timeline relays class + capability id per failing item and
    # counts the classes; prose, paths and extra fields never pass.
    event = {
        "ts": "t",
        "role": "WRITER",
        "kind": "NOTE",
        "detail": "GRADER: NOT_CERTIFIED: -",
        "payload": {"grader_tier": "NOT_CERTIFIED", "grader_items": ["SECRETITEM"], "grader_reasons": reasons},
    }
    red = pilot.redact_event(event)
    assert red["grader_reasons"] == expected_pairs
    assert red["grader_class_counts"] == expected_counts
    target = tmp_path / "timeline.jsonl"
    assert pilot.write_pilot_timeline([event], target)
    line = pilot.render_timeline([event])[0]
    for cls, n in expected_counts.items():
        assert f"{cls}={n}" in line
    for public in (target.read_text(encoding="utf-8"), line):
        for word in ("SECRETPROSE", "SECRETSTRING", "SECRETITEM", "/ws/secret", "Writer said", "\"layer\":"):
            assert word not in public


@pytest.mark.parametrize(
    "payload",
    [{"grader_reasons": "stub_handler"}, {"grader_reasons": None}, {}],
)
def test_no_grader_reasons_leaves_the_line_unchanged(payload):
    event = {"ts": "t", "role": "WRITER", "kind": "NOTE", "detail": "d", "payload": payload}
    red = pilot.redact_event(event)
    assert red["grader_reasons"] == [] and red["grader_class_counts"] == {}
    assert "grader_classes" not in pilot.render_timeline([event])[0]
