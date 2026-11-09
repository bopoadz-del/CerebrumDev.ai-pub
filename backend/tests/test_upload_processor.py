import base64
import os
import sys
import tempfile

import pytest

import app.core.upload_processor  # noqa: F401  dotted token so CI-M associates this file
from app.core import upload_processor
from app.core.session_store import create_session, get_session


@pytest.fixture(autouse=True)
def _chroma_isolation(monkeypatch, tmp_path):
    """Keep Chroma tests from writing into ./storage/chroma or sharing a client."""
    import app.core.chroma_store as chroma_store
    monkeypatch.setattr(chroma_store, "CHROMA_PERSIST_DIR", str(tmp_path / "chroma"))
    chroma_store._chroma_client = None


def _write_doc(path, text):
    """Write ``text`` (paragraphs separated by blank lines) as a one-sheet .xlsx,
    one row per paragraph. The upload allowlist is PDF/DOCX/XLSX (F-10b), so the
    process_upload samples are .xlsx (parsed by _parse_xlsx_local) rather than
    the removed .txt path. Chunking is unaffected: chunk_text splits on newlines
    and the extractor emits one line per row."""
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Doc"
    for para in [p.strip() for p in text.split("\n\n") if p.strip()]:
        ws.append([para])
    wb.save(str(path))


class TestEmbedChunks:
    @pytest.mark.asyncio
    async def test_returns_embeddings_and_meta(self, monkeypatch):
        async def _fake_execute(block, input_data, params=None):
            return {
                "status": "success",
                "result": {
                    "operation": "batch_embed",
                    "backend": "model2vec",
                    "dimensions": 256,
                    "embeddings": [[0.1] * 256, [0.2] * 256],
                    "count": 2,
                },
            }

        monkeypatch.setattr(upload_processor, "_execute_block", _fake_execute)
        embeddings, meta = await upload_processor.embed_chunks(["a", "b"])
        assert len(embeddings) == 2
        assert meta == {
            "provider": "zvec",
            "backend": "model2vec",
            "dimensions": 256,
            "model": "minishlab/potion-base-8M",
        }

    @pytest.mark.asyncio
    async def test_uses_model_field_when_present(self, monkeypatch):
        async def _fake_execute(block, input_data, params=None):
            return {
                "status": "success",
                "result": {
                    "operation": "batch_embed",
                    "backend": "custom",
                    "model": "custom-model",
                    "dimensions": 128,
                    "embeddings": [[0.1] * 128],
                    "count": 1,
                },
            }

        monkeypatch.setattr(upload_processor, "_execute_block", _fake_execute)
        embeddings, meta = await upload_processor.embed_chunks(["a"])
        assert meta["model"] == "custom-model"

    @pytest.mark.asyncio
    async def test_raises_on_mismatched_embedding_count(self, monkeypatch):
        async def _fake_execute(block, input_data, params=None):
            return {
                "status": "success",
                "result": {
                    "backend": "model2vec",
                    "dimensions": 256,
                    "embeddings": [[0.1] * 256],
                },
            }

        monkeypatch.setattr(upload_processor, "_execute_block", _fake_execute)
        # Force the ONNX fallback to fail so embed_chunks raises rather than
        # silently recovering.
        monkeypatch.setattr(upload_processor, "_embed_with_onnx", lambda _chunks: (_ for _ in ()).throw(ValueError("ONNX unavailable")))
        with pytest.raises(ValueError):
            await upload_processor.embed_chunks(["a", "b"])

    @pytest.mark.asyncio
    async def test_raises_on_block_failure_no_zero_vectors(self, monkeypatch):
        async def _fake_execute(block, input_data, params=None):
            raise RuntimeError("zvec unavailable")

        monkeypatch.setattr(upload_processor, "_execute_block", _fake_execute)
        # Force the ONNX fallback to fail so the test proves no zero-vector
        # fallback is written when all embedding backends fail.
        monkeypatch.setattr(upload_processor, "_embed_with_onnx", lambda _chunks: (_ for _ in ()).throw(ValueError("ONNX unavailable")))
        with pytest.raises(ValueError):
            await upload_processor.embed_chunks(["a"])


class TestProcessUpload:
    @pytest.fixture(autouse=True)
    def _use_temp_storage(self, monkeypatch, tmp_path):
        monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))

    @pytest.fixture
    def _fake_fastembed(self, monkeypatch):
        """Replace fastembed.TextEmbedding with a deterministic fake (no network)."""
        import numpy as np

        class _FakeModel:
            def __init__(self, model_name):
                self.model_name = model_name

            def embed(self, texts):
                # Deterministic 384-dim float vectors; stable but not semantic.
                for i, text in enumerate(texts):
                    rng = __import__("random").Random(hash(text) & 0xFFFFFFFF)
                    vec = np.array([rng.uniform(-0.5, 0.5) for _ in range(384)], dtype=np.float32)
                    # Give each chunk a slightly different norm so retrieval has signal.
                    vec *= 1.0 + 0.1 * i
                    yield vec

        class _FakeTextEmbedding:
            def __init__(self, model_name):
                self._model = _FakeModel(model_name)

            def embed(self, texts):
                return self._model.embed(texts)

        monkeypatch.setattr(
            upload_processor,
            "_onnx_embed_model",
            lambda: _FakeTextEmbedding(upload_processor.ONNX_EMBED_MODEL),
        )

    @pytest.mark.asyncio
    async def test_captures_embedding_meta(self, monkeypatch, tmp_path):
        async def _fake_embed(chunks):
            return [[float(i)] * 8 for i in range(len(chunks))], {
                "provider": "zvec",
                "backend": "model2vec",
                "dimensions": 8,
                "model": "minishlab/potion-base-8M",
            }

        monkeypatch.setattr(upload_processor, "embed_chunks", _fake_embed)

        create_session("sess_embed_ok", "u1")
        txt = tmp_path / "doc.xlsx"
        _write_doc(txt, "Line one.\n\nLine two.\n\nLine three.")
        await upload_processor.process_upload("sess_embed_ok", [str(txt)])

        state = get_session("sess_embed_ok")
        assert state.index_status is None
        assert state.embedding_meta == {
            "provider": "zvec",
            "backend": "model2vec",
            "dimensions": 8,
            "model": "minishlab/potion-base-8M",
        }
        assert len(state.embeddings) == state.upload.total_chunks
        assert state.upload.status == "completed"
        # Diagnostic (ORDERS_1810Z): the upload stamps the process that persisted
        # the chunks, as host:pid. Dropping the stamp leaves it None.
        assert state.upload.worker and ":" in state.upload.worker

    @pytest.mark.asyncio
    async def test_uses_onnx_fallback_when_zvec_fails(self, monkeypatch, tmp_path, _fake_fastembed):
        """When zvec raises, the default fastembed ONNX backend indexes successfully."""
        async def _failing_zvec(block, input_data, params=None):
            raise RuntimeError("zvec block unavailable")

        monkeypatch.setattr(upload_processor, "_execute_block", _failing_zvec)

        create_session("sess_onnx_fallback", "u1")
        txt = tmp_path / "doc.xlsx"
        _write_doc(
            txt,
            "The quick brown fox jumps over the lazy dog.\n\n"
            "A second paragraph provides more content to chunk.",
        )
        await upload_processor.process_upload("sess_onnx_fallback", [str(txt)])

        state = get_session("sess_onnx_fallback")
        assert state.index_status is None
        assert state.embedding_meta is not None
        assert state.embedding_meta["provider"] == "fastembed"
        assert state.embedding_meta["backend"] == "fastembed"
        assert state.embedding_meta["model"] == upload_processor.ONNX_EMBED_MODEL
        assert state.embedding_meta["dimensions"] == 384
        assert len(state.embeddings) == state.upload.total_chunks
        assert all(len(e) == 384 for e in state.embeddings)
        assert any(isinstance(v, float) and v != 0.0 for e in state.embeddings for v in e)
        assert state.upload.status == "completed"

    @pytest.mark.asyncio
    async def test_onnx_fallback_reupload_replaces_previous_chunks(self, monkeypatch, tmp_path, _fake_fastembed):
        """Re-uploading the same file with ONNX fallback evicts stale chunks."""
        import app.core.chroma_store as chroma_store

        async def _failing_zvec(block, input_data, params=None):
            raise RuntimeError("zvec block unavailable")

        monkeypatch.setattr(upload_processor, "_execute_block", _failing_zvec)

        txt = tmp_path / "doc.xlsx"
        long_paragraphs = [
            f"Paragraph {i} has a substantial amount of content so that the chunking "
            "logic, which targets roughly eight hundred characters per chunk, is forced "
            "to split this text into multiple pieces rather than keeping everything in a "
            "single chunk. We need at least three chunks for the stale-tail assertion."
            for i in range(5)
        ]
        _write_doc(txt, "\n\n".join(long_paragraphs))

        create_session("sess_onnx_reupload", "u1")
        await upload_processor.process_upload("sess_onnx_reupload", [str(txt)])
        first_state = get_session("sess_onnx_reupload")
        first_count = chroma_store.count_chunks("sess_onnx_reupload")
        assert first_count == len(first_state.chunks)
        assert first_state.embedding_meta["provider"] == "fastembed"

        _write_doc(txt, "Only one paragraph remains.")
        await upload_processor.process_upload("sess_onnx_reupload", [str(txt)])
        second_state = get_session("sess_onnx_reupload")
        second_count = chroma_store.count_chunks("sess_onnx_reupload")
        assert second_count == len(second_state.chunks)
        assert second_count < first_count

    @pytest.mark.asyncio
    async def test_uses_hash_fallback_and_degraded_on_total_embed_failure(self, monkeypatch, tmp_path):
        async def _fake_embed(chunks):
            raise ValueError("all embedding backends unavailable")

        monkeypatch.setattr(upload_processor, "embed_chunks", _fake_embed)

        create_session("sess_embed_fail", "u1")
        txt = tmp_path / "doc.xlsx"
        _write_doc(txt, "Line one.\n\nLine two.")
        await upload_processor.process_upload("sess_embed_fail", [str(txt)])

        state = get_session("sess_embed_fail")
        assert state.index_status == "degraded"
        assert len(state.embeddings) == len(state.chunks)
        assert state.embedding_meta["provider"] == upload_processor.FALLBACK_EMBED_PROVIDER
        assert "RAG unavailable" in state.upload.message
        assert state.upload.status == "completed_with_warnings"

    @pytest.mark.asyncio
    async def test_reupload_after_degraded_persists_to_chroma(self, monkeypatch, tmp_path):
        """After a degraded (hash-fallback) upload, a successful re-upload resets index_status and persists."""
        import app.core.chroma_store as chroma_store

        async def _fail_embed(chunks):
            raise ValueError("all embedding backends unavailable")

        monkeypatch.setattr(upload_processor, "embed_chunks", _fail_embed)

        create_session("sess_reupload", "u1")
        txt = tmp_path / "doc.xlsx"
        _write_doc(txt, "First upload.\n\nSecond paragraph.")
        await upload_processor.process_upload("sess_reupload", [str(txt)])

        degraded = get_session("sess_reupload")
        assert degraded.index_status == "degraded"
        assert degraded.upload.indexed_collection == chroma_store.collection_name("sess_reupload")
        first_count = chroma_store.count_chunks("sess_reupload")
        assert first_count == len(degraded.chunks)

        async def _ok_embed(chunks):
            return [[float(i)] * 8 for i in range(len(chunks))], {
                "provider": "zvec",
                "backend": "model2vec",
                "dimensions": 8,
                "model": "minishlab/potion-base-8M",
            }

        monkeypatch.setattr(upload_processor, "embed_chunks", _ok_embed)
        await upload_processor.process_upload("sess_reupload", [str(txt)])

        state = get_session("sess_reupload")
        assert state.index_status is None
        assert state.upload.status == "completed"
        assert state.upload.indexed_collection == chroma_store.collection_name("sess_reupload")
        assert chroma_store.count_chunks("sess_reupload") == len(state.chunks)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "a_name,a_text,b_name,b_text",
        [
            ("alpha.xlsx", "Alpha one.\n\nAlpha two.", "beta.xlsx", "Beta one.\n\nBeta two."),
            (
                "contract.xlsx",
                "Clause 14.3.\n\nRetention five percent.",
                "boq.xlsx",
                "Accepted amount.\n\nLD rate.",
            ),
        ],
    )
    async def test_sequential_uploads_append_do_not_replace(
        self, monkeypatch, tmp_path, a_name, a_text, b_name, b_text
    ):
        """Task 3: attaching a second file adds beside the first, not over it.
        A re-upload of the SAME filename still evicts (see the stale-tail test);
        a DIFFERENT filename must survive so the assistant sees both."""

        async def _fake_embed(chunks):
            return [[float(i)] * 8 for i in range(len(chunks))], {
                "backend": "model2vec",
                "dimensions": 8,
                "model": "minishlab/potion-base-8M",
            }

        monkeypatch.setattr(upload_processor, "embed_chunks", _fake_embed)

        file_a = tmp_path / a_name
        _write_doc(file_a, a_text)
        file_b = tmp_path / b_name
        _write_doc(file_b, b_text)

        sid = f"sess_append_{a_name.split('.')[0]}"
        create_session(sid, "u1")
        await upload_processor.process_upload(sid, [str(file_a)])
        await upload_processor.process_upload(sid, [str(file_b)])

        state = get_session(sid)
        names = sorted(set(state.source_files))
        assert names == sorted([a_name, b_name])
        # chunks/embeddings stay parallel after the append (packager invariant).
        assert len(state.embeddings) == len(state.chunks)
        joined = "\n".join(state.chunks)
        assert a_text.split("\n\n")[0] in joined
        assert b_text.split("\n\n")[0] in joined

    @pytest.mark.asyncio
    async def test_two_files_chunks_coexist(self, monkeypatch, tmp_path):
        """Chunks from two different source files coexist in one session collection."""
        import app.core.chroma_store as chroma_store

        async def _fake_embed(chunks):
            return [[float(i)] * 8 for i in range(len(chunks))], {
                "backend": "model2vec",
                "dimensions": 8,
                "model": "minishlab/potion-base-8M",
            }

        monkeypatch.setattr(upload_processor, "embed_chunks", _fake_embed)

        file_a = tmp_path / "alpha.xlsx"
        _write_doc(file_a, "Alpha one.\n\nAlpha two.\n\nAlpha three.")
        file_b = tmp_path / "beta.xlsx"
        _write_doc(file_b, "Beta one.\n\nBeta two.")

        create_session("sess_two_files", "u1")
        await upload_processor.process_upload("sess_two_files", [str(file_a), str(file_b)])

        state = get_session("sess_two_files")
        assert state.upload.status == "completed"
        assert chroma_store.count_chunks("sess_two_files") == len(state.chunks)

        reloaded = chroma_store.load_session_upload("sess_two_files")
        assert reloaded is not None
        assert sorted(reloaded["source_files"]) == ["alpha.xlsx", "beta.xlsx"]

    @pytest.mark.parametrize("original_name", ["harbourline_contract.xlsx", "harbourline_boq.xlsx"])
    @pytest.mark.asyncio
    async def test_source_files_carry_the_original_name_not_the_uuid_prefix(self, monkeypatch, tmp_path, original_name):
        """F-10c-a: source_files carry the client's filename, not the on-disk
        uuid-prefixed name, so document:<filename> citations match the caller."""

        async def _fake_embed(chunks):
            return [[float(i)] * 8 for i in range(len(chunks))], {
                "backend": "model2vec",
                "dimensions": 8,
                "model": "minishlab/potion-base-8M",
            }

        monkeypatch.setattr(upload_processor, "embed_chunks", _fake_embed)

        # Save with a uuid-prefixed on-disk name, as the upload route does.
        prefixed = tmp_path / f"{'a' * 32}_{original_name}"
        _write_doc(prefixed, "Clause 14.3.\n\nRetention five percent.")

        create_session("sess_f10c_name", "u1")
        await upload_processor.process_upload(
            "sess_f10c_name", [str(prefixed)], [original_name]
        )

        state = get_session("sess_f10c_name")
        assert state.source_files == [original_name]

    @pytest.mark.asyncio
    async def test_reupload_same_file_with_fewer_chunks_removes_stale_tail(self, monkeypatch, tmp_path):
        """Re-uploading the same file with fewer chunks leaves no stale tail chunks."""
        import app.core.chroma_store as chroma_store

        async def _fake_embed(chunks):
            return [[float(i)] * 8 for i in range(len(chunks))], {
                "backend": "model2vec",
                "dimensions": 8,
                "model": "minishlab/potion-base-8M",
            }

        monkeypatch.setattr(upload_processor, "embed_chunks", _fake_embed)

        txt = tmp_path / "doc.xlsx"
        # Make each paragraph long enough that chunk_text splits into >2 chunks.
        long_paragraphs = [
            f"Paragraph {i} has a substantial amount of content so that the chunking "
            "logic, which targets roughly eight hundred characters per chunk, is forced "
            "to split this text into multiple pieces rather than keeping everything in a "
            "single chunk. We need at least three chunks for the stale-tail assertion."
            for i in range(10)
        ]
        _write_doc(txt, "\n\n".join(long_paragraphs))

        create_session("sess_stale_tail", "u1")
        await upload_processor.process_upload("sess_stale_tail", [str(txt)])
        first_state = get_session("sess_stale_tail")
        first_count = chroma_store.count_chunks("sess_stale_tail")
        assert first_count == len(first_state.chunks)
        assert first_count > 2

        # Truncate the same file to fewer paragraphs and re-upload.
        _write_doc(txt, "Only one paragraph remains.\n\nSecond final paragraph.")
        await upload_processor.process_upload("sess_stale_tail", [str(txt)])

        second_state = get_session("sess_stale_tail")
        second_count = chroma_store.count_chunks("sess_stale_tail")
        assert second_count == len(second_state.chunks)
        assert second_count < first_count

        # No extra chunks from the first upload should still be present.
        docs, _, _ = chroma_store.load_chunks("sess_stale_tail")
        assert len(docs) == second_count


class TestParsePdfWithMarker:
    def test_returns_empty_when_marker_not_installed(self, monkeypatch):
        """If marker-pdf is missing, the helper should gracefully fall back."""
        monkeypatch.setattr(upload_processor, "MARKER_ENABLED", True)
        monkeypatch.setitem(sys.modules, "marker.converters.pdf", None)
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"%PDF-1.4 fake pdf content")
            path = f.name
        try:
            # marker is an optional dependency; if it's absent we expect "".
            text = upload_processor._parse_pdf_with_marker(path)
            assert text == ""
        finally:
            os.unlink(path)

    def test_returns_empty_when_disabled(self, monkeypatch):
        monkeypatch.setattr(upload_processor, "MARKER_ENABLED", False)
        text = upload_processor._parse_pdf_with_marker("any.pdf")
        assert text == ""


class TestFileType:
    def test_pdf_extension(self):
        assert upload_processor._file_type("/docs/manual.pdf") == "pdf"

    def test_docx_extension(self):
        assert upload_processor._file_type("/docs/manual.docx") == "docx"

    @pytest.mark.parametrize("path", ["/docs/scan.png", "/docs/notes.txt"])
    def test_disallowed_types_are_unknown(self, path):
        # F-10b: images and .txt/.md/.csv left the allowlist, so _file_type no
        # longer classifies them (the "image"/"text" branches are gone).
        assert upload_processor._file_type(path) == "unknown"

    @pytest.mark.parametrize("path", ["/docs/budget.xlsx", "/tmp/Q3-Costs.xlsx"])
    def test_xlsx_extension(self, path):
        assert upload_processor._file_type(path) == "xlsx"

    def test_unknown_extension(self):
        assert upload_processor._file_type("/docs/data.xyz") == "unknown"


@pytest.mark.asyncio
async def test_process_upload_unknown_session_returns_cleanly():
    # F-10c: the unknown-session guard returns early; dropping it would
    # AttributeError on a None state. No session is created for this id, so a
    # clean return (no exception) proves the guard runs.
    from app.core.session_store import get_session

    await upload_processor.process_upload("sess_f10c_unknown_xyz", ["/tmp/nope.pdf"])
    assert get_session("sess_f10c_unknown_xyz") is None


class TestParseXlsx:
    """F-10b: .xlsx extraction -- each sheet headed by its name, rows to text."""

    @staticmethod
    def _make_xlsx(path, sheets):
        import openpyxl

        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        for name, rows in sheets.items():
            ws = wb.create_sheet(title=name)
            for row in rows:
                ws.append(row)
        wb.save(path)

    @pytest.mark.parametrize(
        "sheets, must_contain",
        [
            (
                {"Budget": [["Item", "Cost"], ["Crane", 1200]]},
                ["# Budget", "Item", "Cost", "Crane", "1200"],
            ),
            (
                {"Schedule": [["Task", "Days"], ["Pour", 5]], "Rates": [["Rate", 0.1]]},
                ["# Schedule", "Task", "Pour", "5", "# Rates", "Rate", "0.1"],
            ),
        ],
    )
    def test_parse_xlsx_local_two_workbooks(self, tmp_path, sheets, must_contain):
        from app.core.upload_processor import _parse_xlsx_local

        p = tmp_path / "book.xlsx"
        self._make_xlsx(str(p), sheets)
        text = _parse_xlsx_local(str(p))
        for token in must_contain:
            assert token in text, (token, text)

    @pytest.mark.asyncio
    async def test_parse_document_routes_an_xlsx(self, tmp_path):
        from app.core.upload_processor import parse_document

        p = tmp_path / "data.xlsx"
        self._make_xlsx(str(p), {"S1": [["alpha", "beta"]]})
        text = await parse_document(str(p))
        assert "# S1" in text and "alpha" in text and "beta" in text


class TestChunkText:
    def test_chunks_by_paragraph(self):
        text = "\n\n".join([f"Paragraph {i}" for i in range(20)])
        chunks = upload_processor.chunk_text(text, max_chars=100, overlap=10)
        assert len(chunks) > 1
        assert all(len(c) <= 100 for c in chunks)

    def test_single_long_paragraph_is_kept(self):
        # The chunker does not hard-split mid-paragraph; it emits one chunk.
        text = "a" * 500
        chunks = upload_processor.chunk_text(text, max_chars=200, overlap=0)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_whitespace_only_returns_empty(self):
        text = "   "
        chunks = upload_processor.chunk_text(text, max_chars=200, overlap=0)
        assert chunks == []


class TestParseDocument:
    # The .txt parse test was removed with the "text" branch (F-10b): the upload
    # allowlist is PDF/DOCX/XLSX; xlsx routing is covered by
    # TestParseXlsx::test_parse_document_routes_an_xlsx.
    @pytest.mark.asyncio
    async def test_pdf_prefers_marker_block_then_pdf_block(self, monkeypatch):
        """The PDF parser should try the marker block, then the pdf block, then fallbacks."""
        calls = []

        async def _fake_execute(block, input_data, params=None):
            calls.append(block)
            if block == "marker":
                return {"status": "success", "result": {"text": "# Markdown"}}
            raise RuntimeError("should not reach generic pdf block")

        monkeypatch.setattr(upload_processor, "_execute_block", _fake_execute)
        monkeypatch.setattr(upload_processor, "MARKER_ENABLED", True)

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"%PDF-1.4 fake")
            path = f.name
        try:
            text = await upload_processor.parse_document(path)
            assert "# Markdown" in text
            assert calls == ["marker"]
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_pdf_falls_back_to_local_when_blocks_fail(self, monkeypatch):
        """If all Cerebrum-Blocks calls fail, local parsers are attempted."""
        calls = []

        async def _fake_execute(block, input_data, params=None):
            calls.append(block)
            raise RuntimeError(f"{block} unavailable")

        monkeypatch.setattr(upload_processor, "_execute_block", _fake_execute)
        monkeypatch.setattr(upload_processor, "MARKER_ENABLED", True)
        monkeypatch.setattr(upload_processor, "_parse_pdf_with_marker", lambda path: "")
        monkeypatch.setattr(upload_processor, "_parse_pdf_local", lambda path: "local pdf text")

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"%PDF-1.4 fake")
            path = f.name
        try:
            text = await upload_processor.parse_document(path)
            assert text == "local pdf text"
            assert calls == ["marker", "pdf"]
        finally:
            os.unlink(path)


class TestProcessUploadTerminalGuarantee:
    """A death anywhere inside the pipeline must still leave a TERMINAL
    upload.status. A strand at "processing" is what wedged the Floor behind a
    permanent 409 "Upload already in progress" (mobile incident 2026-11-02):
    the POST route's guard reads upload.status, so the terminal write is the
    contract of this task, not a nicety."""

    @pytest.fixture(autouse=True)
    def _use_temp_storage(self, monkeypatch, tmp_path):
        monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))

    @pytest.mark.asyncio
    @pytest.mark.parametrize("crash_point", ["embed", "store"])
    async def test_a_crash_records_failed_never_a_stuck_processing(
        self, monkeypatch, tmp_path, crash_point
    ):
        # Two different unguarded failure points: a non-ValueError from the
        # embedding stage, and a vector-store write blowing up after a clean
        # embed. Both used to leave upload.status == "processing" forever.
        if crash_point == "embed":
            async def _boom(chunks):
                raise RuntimeError("embedding backend exploded")

            monkeypatch.setattr(upload_processor, "embed_chunks", _boom)
        else:
            async def _ok(chunks):
                return [[0.0] * 8 for _ in chunks], {
                    "provider": "test", "backend": "fake",
                    "dimensions": 8, "model": "m",
                }

            def _store_boom(*args, **kwargs):
                raise RuntimeError("vector store exploded")

            monkeypatch.setattr(upload_processor, "embed_chunks", _ok)
            monkeypatch.setattr(upload_processor, "store_chunks", _store_boom)

        sid = f"sess_crash_{crash_point}"
        create_session(sid, "u1")
        doc = tmp_path / "doc.xlsx"
        _write_doc(doc, "Line one.\n\nLine two.")
        with pytest.raises(RuntimeError):
            await upload_processor.process_upload(sid, [str(doc)])

        state = get_session(sid)
        assert state.upload.status == "failed"  # terminal — never "processing"
        assert "Upload processing failed" in state.upload.message
        assert "RuntimeError" in state.upload.message


class TestParseDocumentContract:
    """F4 step 2: the block input carries content, not the EFS file_path."""

    @pytest.mark.parametrize(
        "doc_type, raw",
        [
            ("pdf", b"%PDF-1.4 fake pdf bytes"),
            ("docx", b"PK\x03\x04 fake docx bytes"),
        ],
    )
    @pytest.mark.asyncio
    async def test_parse_document_sends_content_not_file_path(
        self, tmp_path, monkeypatch, doc_type, raw
    ):
        path = tmp_path / f"sample.{doc_type}"
        path.write_bytes(raw)
        captured = {}

        async def _fake_execute(block, input_data, params=None):
            captured["block"] = block
            captured["input"] = input_data
            return {"status": "success", "result": {"text": "extracted"}}

        monkeypatch.setattr(upload_processor, "_execute_block", _fake_execute)
        text = await upload_processor.parse_document(str(path))

        assert text == "extracted"
        assert "file_path" not in captured["input"]
        assert captured["input"]["filename"] == f"sample.{doc_type}"
        assert base64.b64decode(captured["input"]["content"]) == raw
