"""F-10c-b: the session's uploaded chunks seed the product's own corpus through
the kernel ingestion path (no second parser), append-only. Full-dotted import
so the mutation gate selects this module.
"""
from __future__ import annotations

import pytest

import app.factory.build.upload_corpus_seed as seed_mod  # noqa: F401  dotted token for CI-M
from app.factory.build.upload_corpus_seed import (
    seed_product_corpus_from_session,
    seed_uploaded_corpus_best_effort,
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


@pytest.mark.parametrize("user_id, seeds", [("tenant-owner", True), ("", False)])
def test_best_effort_hook_seeds_only_with_an_owner_tenant(monkeypatch, user_id, seeds):
    calls = []
    monkeypatch.setattr(
        seed_mod, "seed_product_corpus_from_session",
        lambda state, identity, **k: calls.append(identity) or {"state": "indexed"},
    )
    s = SessionState(session_id="sess_hook", user_id=user_id)
    s.chunks = ["ACA is SAR 39,098,392.98."]
    s.source_files = ["contract.pdf"]
    seed_uploaded_corpus_best_effort(s)
    assert calls == (["tenant-owner"] if seeds else [])


def test_best_effort_hook_never_raises_into_the_build(monkeypatch):
    def _boom(state, identity, **k):
        raise RuntimeError("retrieval backend down")

    monkeypatch.setattr(seed_mod, "seed_product_corpus_from_session", _boom)
    s = SessionState(session_id="sess_hook2", user_id="t")
    s.chunks = ["x"]
    s.source_files = ["a.pdf"]
    # Must swallow the error -> the build is never broken by seeding.
    assert seed_uploaded_corpus_best_effort(s) is None
