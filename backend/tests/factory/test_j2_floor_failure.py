"""J2 (ORDERS_04NOV_2000Z): the Floor shows a plain-English summary + a ref, not
the raw payload. _floor_failure attaches code / summary / ref to a failure row;
the full detail stays on the row for the build record (J3), never in the summary.

Full-dotted import so the mutation gate (CI-M) selects this module.
"""
from __future__ import annotations

import pytest

import app.factory.build_jobs as build_jobs  # noqa: F401  dotted token for CI-M
from app.factory.build_jobs import _FAILURE_SUMMARIES, _failure_ref, _floor_failure


def _row(**over):
    row = {
        "phase": "WRITER",
        "location": "WRITER",
        "reason": "writer_behaviour_failed: no capability accepted its own schema",
        "timestamp": "2026-11-04T20:00:00Z",
        "detail": '{"error":"RuntimeError: Unknown action: None"}',
    }
    row.update(over)
    return row


def test_none_stays_none():
    # No failure -> nothing to enrich; the Floor shows no failure line.
    assert _floor_failure(None) is None


@pytest.mark.parametrize(
    "reason, expected_code",
    [
        ("writer_behaviour_failed: no capability accepted its own schema", "writer_behaviour_failed"),
        ("docker_unavailable", "docker_unavailable"),
    ],
)
def test_code_is_the_stable_token_before_the_colon(reason, expected_code):
    # The compound reason the owner photographed collapses to a stable code the
    # UI can branch on -- never the whole technical phrase.
    assert _floor_failure(_row(reason=reason))["code"] == expected_code


@pytest.mark.parametrize(
    "code, expected",
    [
        ("docker_unavailable", _FAILURE_SUMMARIES["docker_unavailable"]),
        ("writer_no_output", _FAILURE_SUMMARIES["writer_no_output"]),
        ("packager_failed", _FAILURE_SUMMARIES["packager_failed"]),
    ],
)
def test_mapped_code_gets_its_sentence(code, expected):
    f = _floor_failure(_row(reason=code))
    assert f["summary"] == expected


def test_unmapped_code_falls_back_to_a_generic_sentence_with_no_payload():
    f = _floor_failure(_row(reason="some_brand_new_kind", location="PACKAGER"))
    # A code with no table entry still gets a clean sentence -- the generic
    # fallback names the step, never the payload.
    assert "PACKAGER" in f["summary"]
    assert f["summary"].endswith(".")
    assert "{" not in f["summary"] and "RuntimeError" not in f["summary"]


@pytest.mark.parametrize("code", ["some_brand_new_kind", "an_unmapped_producer_kind"])
def test_the_generic_fallback_records_which_code_it_fell_back_from(code, caplog):
    # The fallback is not silent: it logs the unmapped code (same channel J3
    # uses) so the table can grow from evidence. packager_failed is NOT used
    # here -- s4(d) maps it, so it no longer takes the fallback path.
    import logging as _logging

    with caplog.at_level(_logging.INFO):
        _floor_failure(_row(reason=code))
    assert any(getattr(r, "code", None) == code for r in caplog.records)


def test_summary_never_carries_the_raw_payload():
    # The owner's exact photograph: braces + RuntimeError must not reach the
    # one-line summary for ANY row.
    f = _floor_failure(_row())
    assert "{" not in f["summary"]
    assert "RuntimeError" not in f["summary"]
    assert "Unknown action" not in f["summary"]


@pytest.mark.parametrize(
    "detail",
    [
        '{"error":"RuntimeError: Unknown action: None"}',
        "plain failure text without braces",
    ],
)
def test_detail_is_retained_for_the_record(detail):
    # _floor_failure leaves detail on the row (failure_triple reads it as its
    # finding fallback); the wire strip removes it -- see the boundary test.
    assert _floor_failure(_row(detail=detail))["detail"] == detail


@pytest.mark.parametrize(
    "row",
    [
        _row(),
        _row(reason="packager_failed", timestamp="2026-11-05T01:00:00Z", phase="PACKAGER"),
    ],
)
def test_ref_is_short_and_stable(row):
    # Same row -> same ref on every poll (so the Floor's ref matches the record).
    assert _failure_ref(row) == _failure_ref(dict(row))
    assert len(_failure_ref(row)) == 10


@pytest.mark.parametrize(
    "a_reason, b_reason",
    [
        ("writer_no_output", "docker_unavailable"),
        ("timeout", "build_thread_crashed"),
    ],
)
def test_distinct_failures_get_distinct_refs(a_reason, b_reason):
    # Two different failures never collide on the same ref.
    assert _failure_ref(_row(reason=a_reason)) != _failure_ref(_row(reason=b_reason))


@pytest.mark.parametrize(
    "code, detail",
    [
        ("writer_no_output", "zero agent-authored artifacts TRIPWIRE_7f3a9c"),
        ("writer_behaviour_failed", '{"error":"RuntimeError: Unknown action: None"} TRIPWIRE_7f3a9c'),
    ],
)
def test_the_detail_reaches_the_record_and_never_the_wire(tmp_path, code, detail):
    """ORDERS_04NOV_2300Z s3 GATE: the full detail is filed in the record under
    the ref the Floor shows, and is nowhere in what the wire serialises.

    build_status's dict is exactly what every wire exit serialises (the route
    and the chat responses that embed ``"build": build_status(...)``), so
    json.dumps(build_status(out)) is the whole response body -- a stronger
    check than one route, and without the session/route fixture."""
    import json

    from app.factory.build.authority import BuildRole
    from app.factory.build.ledger import BuildLedger, EventKind
    from app.factory.build_jobs import build_status, failure_records

    out = tmp_path / "prod"
    out.mkdir()
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id="boundary", inputs_hash="h")
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER, detail="WRITER")
    ledger.append(
        EventKind.GATE_FAILED,
        role=BuildRole.WRITER,
        detail=detail,
        payload={"reason": code, "location": "WRITER"},
    )
    # No terminal event: a live rework, the owner's actual scenario.

    status = build_status(out)
    ref = status["failure"]["ref"]

    # J3: the full detail IS filed, under the ref the Floor shows.
    entry = next(r for r in failure_records(out) if r["ref"] == ref)
    assert "TRIPWIRE_7f3a9c" in json.dumps(entry["detail"])

    # J2: and it is nowhere in what the browser is sent.
    assert "TRIPWIRE_7f3a9c" not in json.dumps(status)


@pytest.mark.parametrize("not_a_dict", [["a", "list"], "a string"])
def test_strip_floor_detail_passes_a_non_dict_through_untouched(not_a_dict):
    # The isinstance guard must hold: a non-dict status is returned as-is, never
    # .get()'d (which would crash). Pins the guard against drop_rule.
    from app.factory.build_jobs import _strip_floor_detail

    assert _strip_floor_detail(not_a_dict) == not_a_dict


@pytest.mark.parametrize("sub", ["no_ledger_a", "no_ledger_b"])
def test_failure_records_is_empty_without_a_ledger(tmp_path, sub):
    # No ledger (or a torn one) yields no record, not a 500.
    from app.factory.build_jobs import failure_records

    out = tmp_path / sub
    out.mkdir()
    assert failure_records(out) == []


@pytest.mark.parametrize(
    "raw, expect",
    [
        (
            'track_management: baseline POST returned HTTP 400: {"ok":false,"error":"x"}',
            "track_management: baseline POST returned HTTP 400",
        ),
        (
            "block: called with no action= keyword. Answered 'abc' (CONTRACT: unknown action)",
            "block: called with no action= keyword.",
        ),
    ],
)
def test_floor_safe_text_cuts_the_payload_keeps_the_prefix(raw, expect):
    from app.factory.build_jobs import _floor_safe_text

    out = _floor_safe_text(raw)
    assert out == expect
    assert "{" not in out and "Answered" not in out


@pytest.mark.parametrize("val", [12, None])
def test_floor_safe_text_passes_non_strings_through(val):
    from app.factory.build_jobs import _floor_safe_text

    assert _floor_safe_text(val) == val


@pytest.mark.parametrize(
    "check, clean",
    [
        (
            'track_management: baseline POST returned HTTP 400: {"ok":false}',
            "track_management: baseline POST returned HTTP 400",
        ),
        ('workflow refused {"blocks":{}}', "workflow refused"),
    ],
)
def test_strip_floor_detail_cleans_the_decisions_list(check, clean):
    # The rework/decisions list (floorView d.check) carried the raw payload the
    # owner photographed under "REWORK WRITER round 1/2". It is sanitised here.
    from app.factory.build_jobs import _strip_floor_detail

    out = _strip_floor_detail(
        {"decisions": [{"class": "REWORK", "gate": "WRITER", "check": check}]}
    )
    d = out["decisions"][0]
    assert d["check"] == clean
    assert "{" not in d["check"]
    assert d["class"] == "REWORK"  # short fields untouched
