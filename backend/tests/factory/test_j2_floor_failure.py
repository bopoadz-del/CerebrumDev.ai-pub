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


def test_code_is_the_stable_token_before_the_colon():
    f = _floor_failure(_row())
    # The compound reason the owner photographed collapses to a stable code the
    # UI can branch on -- never the whole technical phrase.
    assert f["code"] == "writer_behaviour_failed"


@pytest.mark.parametrize(
    "code, expected",
    [
        ("docker_unavailable", _FAILURE_SUMMARIES["docker_unavailable"]),
        ("writer_no_output", _FAILURE_SUMMARIES["writer_no_output"]),
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


def test_summary_never_carries_the_raw_payload():
    # The owner's exact photograph: braces + RuntimeError must not reach the
    # one-line summary for ANY row.
    f = _floor_failure(_row())
    assert "{" not in f["summary"]
    assert "RuntimeError" not in f["summary"]
    assert "Unknown action" not in f["summary"]


def test_detail_is_retained_for_the_record():
    # J3 files the full detail under the ref; _floor_failure must not drop it.
    f = _floor_failure(_row())
    assert f["detail"] == '{"error":"RuntimeError: Unknown action: None"}'


def test_ref_is_short_and_stable():
    a = _failure_ref(_row())
    b = _failure_ref(_row())
    # Same row -> same ref on every poll (so the Floor's ref matches the record).
    assert a == b
    assert len(a) == 10
    # A different failure yields a different ref.
    assert _failure_ref(_row(reason="other", timestamp="t2")) != a
