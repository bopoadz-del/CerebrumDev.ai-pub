"""F-10c-b: the session's uploaded chunks seed the product's own corpus through
the kernel ingestion path (no second parser), append-only. Full-dotted import
so the mutation gate selects this module.
"""
from __future__ import annotations

import pytest

import app.factory.build.upload_corpus_seed as seed_mod  # noqa: F401  dotted token for CI-M
from app.factory.build.upload_corpus_seed import (
    record_corpus_finding,
    seed_product_corpus_from_session,
    seed_uploaded_corpus,
    session_chunk_rows,
)
from app.models.session import SessionState


def _state(chunks, names):
    s = SessionState(session_id="sess_f10c_b", user_id="u1")
    s.chunks = chunks
    s.source_files = names
    return s


@pytest.mark.parametrize(
    "chunks, names, expect_files",
    [
        ([], [], set()),
        (
            ["LD rate is 0.1%/day.", "ACA is SAR 39,098,392.98.", "Completion 730 days."],
            ["contract.pdf", "contract.pdf", "programme.xlsx"],
            {"contract.pdf", "programme.xlsx"},
        ),
    ],
)
def test_session_chunk_rows_carry_file_labels_or_are_empty(chunks, names, expect_files):
    rows = session_chunk_rows(_state(chunks, names))
    assert {r["file"] for r in rows} == expect_files
    if rows:
        assert len(rows) == len(chunks)
        assert all(r["text"] and r["source_class"] == "project_corpus" for r in rows)
        assert all("location" in r for r in rows)


@pytest.mark.parametrize(
    "chunks, names, should_ingest",
    [
        ([], [], False),
        (["Delay damages 0.1%/day.", "ACA SAR 39,098,392.98."], ["contract.pdf", "boq.xlsx"], True),
    ],
)
def test_seed_calls_ingest_only_when_documents_were_uploaded(chunks, names, should_ingest):
    calls = []

    def _fake_ingest(identity, rows):
        calls.append((identity, rows))
        return {"state": "indexed", "count": len(rows)}

    out = seed_product_corpus_from_session(_state(chunks, names), "tenant-owner", ingest=_fake_ingest)
    if should_ingest:
        assert len(calls) == 1
        identity, rows = calls[0]
        assert identity == "tenant-owner"
        assert {r["file"] for r in rows} == set(names)  # both docs seeded in one append
        assert out["count"] == len(chunks)
    else:
        assert calls == []  # nothing uploaded -> the product corpus is untouched
        assert out is None


def _uploaded_state(user_id="tenant-owner"):
    s = SessionState(session_id="sess_hook", user_id=user_id)
    s.chunks = ["ACA is SAR 39,098,392.98."]
    s.source_files = ["contract.pdf"]
    return s


class _Outcome:
    def __init__(self, state, detail="", count=0):
        self.state, self.detail, self.count = state, detail, count


@pytest.mark.parametrize("count", [1, 3])
def test_seed_uploaded_corpus_success_records_no_finding(count):
    s = _uploaded_state()
    out = seed_uploaded_corpus(s, seed=lambda st, ident: _Outcome("indexed", count=count))
    assert out.state == "indexed"
    assert not s.upload.failed_files  # success -> the user is told nothing bad


@pytest.mark.parametrize("scenario", ["seed_raises", "no_tenant", "not_indexed"])
def test_seed_uploaded_corpus_records_a_visible_finding_on_trouble(scenario):
    s = _uploaded_state(user_id="" if scenario == "no_tenant" else "tenant-owner")

    def _seed(st, ident):
        if scenario == "seed_raises":
            raise RuntimeError("retrieval backend down")
        return _Outcome("not_indexed", detail="RETRIEVAL_BACKEND_NOT_CONFIGURED")

    out = seed_uploaded_corpus(s, seed=_seed)
    # The user is ALWAYS told: a finding string is returned AND shown on the
    # Floor surface (state.upload) -- never a silent None.
    assert isinstance(out, str)
    assert out.startswith("documents were not indexed into the product corpus:")
    assert s.upload.message == out
    assert out in s.upload.failed_files


def test_seed_uploaded_corpus_no_upload_is_a_noop_with_no_finding():
    calls = []
    s = SessionState(session_id="sess_empty", user_id="tenant-owner")  # no chunks
    out = seed_uploaded_corpus(s, seed=lambda st, ident: calls.append(1))
    assert out is None
    assert not calls  # nothing uploaded -> the seed is never invoked
    assert not s.upload.failed_files  # and nothing is flagged


@pytest.mark.parametrize("with_output_dir", [True, False])
def test_record_corpus_finding_writes_the_handoff_ledger_when_there_is_one(tmp_path, with_output_dir):
    from app.factory.build.ledger import BuildLedger

    s = _uploaded_state()
    if with_output_dir:
        out = tmp_path / "ws"
        out.mkdir()
        s.product_design.generation = {"output_dir": str(out)}
    finding = record_corpus_finding(s, "retrieval backend down")
    assert finding.startswith("documents were not indexed into the product corpus:")
    assert s.upload.message == finding
    led = tmp_path / "ws" / "build_ledger.jsonl"
    if with_output_dir:
        notes = [e for e in BuildLedger(led).events() if (e.payload or {}).get("corpus_seed_finding")]
        assert notes and notes[-1].detail == finding
    else:
        assert not led.exists()
