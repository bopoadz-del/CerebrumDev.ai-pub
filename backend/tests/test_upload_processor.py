import os
import sys
import tempfile

import pytest

from app.core import upload_processor
from app.core.session_store import create_session, get_session


@pytest.fixture(autouse=True)
def _chroma_isolation(monkeypatch, tmp_path):
    """Keep Chroma tests from writing into ./storage/chroma or sharing a client."""
    import app.core.chroma_store as chroma_store
    monkeypatch.setattr(chroma_store, "CHROMA_PERSIST_DIR", str(tmp_path / "chroma"))
    chroma_store._chroma_client = None


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
        txt = tmp_path / "doc.txt"
        txt.write_text("Line one.\n\nLine two.\n\nLine three.", encoding="utf-8")
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

    @pytest.mark.asyncio
    async def test_uses_onnx_fallback_when_zvec_fails(self, monkeypatch, tmp_path, _fake_fastembed):
        """When zvec raises, the default fastembed ONNX backend indexes successfully."""
        async def _failing_zvec(block, input_data, params=None):
            raise RuntimeError("zvec block unavailable")

        monkeypatch.setattr(upload_processor, "_execute_block", _failing_zvec)

        create_session("sess_onnx_fallback", "u1")
        txt = tmp_path / "doc.txt"
        txt.write_text(
            "The quick brown fox jumps over the lazy dog.\n\n"
            "A second paragraph provides more content to chunk.",
            encoding="utf-8",
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

        txt = tmp_path / "doc.txt"
        long_paragraphs = [
            f"Paragraph {i} has a substantial amount of content so that the chunking "
            "logic, which targets roughly eight hundred characters per chunk, is forced "
            "to split this text into multiple pieces rather than keeping everything in a "
            "single chunk. We need at least three chunks for the stale-tail assertion."
            for i in range(5)
        ]
        txt.write_text("\n\n".join(long_paragraphs), encoding="utf-8")

        create_session("sess_onnx_reupload", "u1")
        await upload_processor.process_upload("sess_onnx_reupload", [str(txt)])
        first_state = get_session("sess_onnx_reupload")
        first_count = chroma_store.count_chunks("sess_onnx_reupload")
        assert first_count == len(first_state.chunks)
        assert first_state.embedding_meta["provider"] == "fastembed"

        txt.write_text("Only one paragraph remains.", encoding="utf-8")
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
        txt = tmp_path / "doc.txt"
        txt.write_text("Line one.\n\nLine two.", encoding="utf-8")
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
        txt = tmp_path / "doc.txt"
        txt.write_text("First upload.\n\nSecond paragraph.", encoding="utf-8")
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

        file_a = tmp_path / "alpha.txt"
        file_a.write_text("Alpha one.\n\nAlpha two.\n\nAlpha three.", encoding="utf-8")
        file_b = tmp_path / "beta.txt"
        file_b.write_text("Beta one.\n\nBeta two.", encoding="utf-8")

        create_session("sess_two_files", "u1")
        await upload_processor.process_upload("sess_two_files", [str(file_a), str(file_b)])

        state = get_session("sess_two_files")
        assert state.upload.status == "completed"
        assert chroma_store.count_chunks("sess_two_files") == len(state.chunks)

        reloaded = chroma_store.load_session_upload("sess_two_files")
        assert reloaded is not None
        assert sorted(reloaded["source_files"]) == ["alpha.txt", "beta.txt"]

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

        txt = tmp_path / "doc.txt"
        # Make each paragraph long enough that chunk_text splits into >2 chunks.
        long_paragraphs = [
            f"Paragraph {i} has a substantial amount of content so that the chunking "
            "logic, which targets roughly eight hundred characters per chunk, is forced "
            "to split this text into multiple pieces rather than keeping everything in a "
            "single chunk. We need at least three chunks for the stale-tail assertion."
            for i in range(10)
        ]
        txt.write_text("\n\n".join(long_paragraphs), encoding="utf-8")

        create_session("sess_stale_tail", "u1")
        await upload_processor.process_upload("sess_stale_tail", [str(txt)])
        first_state = get_session("sess_stale_tail")
        first_count = chroma_store.count_chunks("sess_stale_tail")
        assert first_count == len(first_state.chunks)
        assert first_count > 2

        # Truncate the same file to fewer paragraphs and re-upload.
        txt.write_text("Only one paragraph remains.\n\nSecond final paragraph.", encoding="utf-8")
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

    def test_image_extension(self):
        assert upload_processor._file_type("/docs/scan.png") == "image"

    def test_text_extension(self):
        assert upload_processor._file_type("/docs/notes.txt") == "text"

    def test_unknown_extension(self):
        assert upload_processor._file_type("/docs/data.xyz") == "unknown"


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
    @pytest.mark.asyncio
    async def test_parse_text_file(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f:
            f.write("Hello from CerebrumDev.ai")
            path = f.name
        try:
            text = await upload_processor.parse_document(path)
            assert "Hello from CerebrumDev.ai" in text
        finally:
            os.unlink(path)

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
