"""F2: a session rehydrated from ChromaDB restores its PER-CHUNK source_files, so
the draft brief still cites document:<filename> after the in-memory state is
evicted (the live gap behind the pilot's brief stage: cited=[], every chunk
grouped under document:uploaded).

Full-dotted imports so the mutation gate (CI-M) selects these modules.
"""
from __future__ import annotations

import pytest

import app.core.chroma_store as chroma_store  # dotted token for CI-M
import app.core.session_store as session_store  # dotted token for CI-M


class _Coll:
    metadata: dict = {}


class _Client:
    def get_collection(self, name):  # noqa: D401 - stub
        return _Coll()


@pytest.mark.parametrize(
    "metadatas, expected",
    [
        # Two files, one with two chunks: source_files is PER-CHUNK and in chunk
        # order -- never deduped/sorted, so repeats and order are preserved and
        # len(source_files) == len(chunks). A sorted-set return reddens this.
        (
            [{"source_file": "contract.pdf"}, {"source_file": "contract.pdf"}, {"source_file": "boq.xlsx"}],
            ["contract.pdf", "contract.pdf", "boq.xlsx"],
        ),
        # A chunk with no recorded source_file keeps its slot as "" (still 1:1).
        (
            [{"source_file": "spec.pdf"}, {}],
            ["spec.pdf", ""],
        ),
    ],
)
def test_load_session_upload_returns_per_chunk_source_files(monkeypatch, metadatas, expected):
    chunks = [f"chunk {i}" for i in range(len(metadatas))]
    monkeypatch.setattr(chroma_store, "collection_exists", lambda sid: True)
    monkeypatch.setattr(chroma_store, "_get_chroma_client", lambda: _Client())
    monkeypatch.setattr(chroma_store, "load_chunks", lambda sid: (chunks, [], list(metadatas)))

    data = chroma_store.load_session_upload("sess_f2")

    assert data is not None
    assert data["source_files"] == expected          # per-chunk, ordered, not deduped
    assert len(data["source_files"]) == len(data["chunks"])  # parallel to chunks


def test_load_session_upload_is_none_without_a_collection(monkeypatch):
    """No collection -> None, BEFORE touching Chroma. Kills the drop_rule on the
    `if not collection_exists: return None` guard: dropping that return falls
    through and returns a dict for a session that has no stored upload."""
    monkeypatch.setattr(chroma_store, "collection_exists", lambda sid: False)
    monkeypatch.setattr(chroma_store, "_get_chroma_client", lambda: _Client())
    monkeypatch.setattr(chroma_store, "load_chunks", lambda sid: (["x"], [], [{"source_file": "a.pdf"}]))
    assert chroma_store.load_session_upload("sess_absent") is None


@pytest.mark.parametrize(
    "source_files",
    [
        ["contract.pdf", "contract.pdf"],  # repeats preserved
        ["a.pdf", "b.pdf", "c.pdf"],
    ],
)
def test_rehydrate_restores_source_files_parallel_to_chunks(monkeypatch, source_files):
    chunks = [f"c{i}" for i in range(len(source_files))]
    # load_session_upload is imported inside _rehydrate_from_chroma from chroma_store.
    monkeypatch.setattr(
        chroma_store, "load_session_upload",
        lambda sid: {
            "chunks": chunks,
            "embeddings": [],
            "embedding_meta": {"provider": "test"},
            "total_chunks": len(chunks),
            "indexed_collection": "col_f2",
            "source_files": list(source_files),
        },
    )
    monkeypatch.setattr(session_store, "save_session_state", lambda s: None)

    state = session_store._rehydrate_from_chroma("sess_f2")

    assert state is not None
    # Dropping the restore line leaves source_files == [] (the model default)
    # while chunks are present -> this assertion reddens that mutant.
    assert state.source_files == source_files
    assert len(state.source_files) == len(state.chunks)
