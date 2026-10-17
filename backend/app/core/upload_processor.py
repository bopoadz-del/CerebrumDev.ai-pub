import hashlib
import os
import httpx
import logging
import mimetypes
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple

from .session_store import get_session, update_session
from .chroma_store import store_chunks, collection_name, _stable_file_hash

logger = logging.getLogger(__name__)

CEREBRUM_API_URL = os.getenv("CEREBRUM_API_URL", "http://localhost:8000")
CEREBRUM_API_KEY = os.getenv("CEREBRUM_API_KEY")
STORAGE_PATH = os.getenv("STORAGE_PATH", "./storage")
MARKER_ENABLED = os.getenv("MARKER_ENABLED", "true").lower() in ("1", "true", "yes")


def _headers() -> Dict[str, str]:
    h = {"Content-Type": "application/json"}
    if CEREBRUM_API_KEY:
        h["Authorization"] = f"Bearer {CEREBRUM_API_KEY}"
    return h


def _session_storage_dir(session_id: str) -> Path:
    path = Path(STORAGE_PATH) / "sessions" / session_id / "files"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _file_type(file_path: str) -> str:
    mime, _ = mimetypes.guess_type(file_path)
    ext = Path(file_path).suffix.lower()
    if mime:
        if mime.startswith("image/"):
            return "image"
        if mime == "application/pdf" or ext == ".pdf":
            return "pdf"
        if mime in (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/msword",
        ) or ext in (".docx", ".doc"):
            return "docx"
    if ext in (".txt", ".md", ".json", ".csv"):
        return "text"
    return "unknown"


async def _execute_block(block: str, input_data: Any, params: Optional[Dict] = None) -> Dict[str, Any]:
    """Call a Cerebrum-Blocks block via /v1/execute."""
    url = f"{CEREBRUM_API_URL}/v1/execute"
    payload = {"block": block, "input": input_data}
    if params:
        payload["params"] = params
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(url, json=payload, headers=_headers())
        resp.raise_for_status()
        return resp.json()


async def parse_document(file_path: str) -> str:
    """Extract text from a document using Cerebrum-Blocks blocks with local fallbacks."""
    doc_type = _file_type(file_path)
    text = ""

    if doc_type == "pdf":
        # 1. Prefer the Cerebrum-Blocks marker block for high-quality Markdown.
        if not text.strip() and MARKER_ENABLED:
            try:
                result = await _execute_block("marker", {"file_path": file_path})
                text = _extract_text_from_result(result)
                logger.info("Parsed %s with Cerebrum-Blocks marker block", file_path)
            except Exception as exc:
                logger.warning("Marker block failed for %s: %s", file_path, exc)

        # 2. Fall back to the generic PDF block.
        if not text.strip():
            try:
                result = await _execute_block("pdf", {"file_path": file_path})
                text = _extract_text_from_result(result)
            except Exception as exc:
                logger.warning("PDF block failed for %s: %s", file_path, exc)

        # 3. Local Marker if the remote blocks didn't work.
        if not text.strip():
            text = _parse_pdf_with_marker(file_path)

        # 4. Final pypdf fallback.
        if not text.strip():
            text = _parse_pdf_local(file_path)

    elif doc_type == "image":
        try:
            result = await _execute_block("ocr", {"file_path": file_path})
            text = _extract_text_from_result(result)
        except Exception as exc:
            logger.warning("OCR block failed for %s: %s", file_path, exc)

    elif doc_type == "docx":
        try:
            result = await _execute_block("pdf", {"file_path": file_path})
            text = _extract_text_from_result(result)
        except Exception as exc:
            logger.warning("DOCX block failed for %s: %s", file_path, exc)
        if not text.strip():
            text = _parse_docx_local(file_path)

    elif doc_type == "text":
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()

    else:
        # Try OCR / PDF block as a last resort
        try:
            result = await _execute_block("pdf", {"file_path": file_path})
            text = _extract_text_from_result(result)
        except Exception as exc:
            logger.warning("Fallback block failed for %s: %s", file_path, exc)

    return text


def _extract_text_from_result(result: Dict[str, Any]) -> str:
    """Pull text out of a Cerebrum-Blocks execute response."""
    if result.get("status") == "error":
        raise RuntimeError(result.get("result", "block error"))
    data = result.get("result", {})
    if isinstance(data, str):
        return data
    if isinstance(data, list):
        return "\n".join(str(x) for x in data)
    # Common shapes
    for key in ("text", "content", "extracted_text", "ocr_text", "pages"):
        if key in data:
            val = data[key]
            if isinstance(val, list):
                return "\n".join(str(x) for x in val)
            return str(val)
    return str(data)


def _parse_pdf_with_marker(file_path: str) -> str:
    """Convert PDF to Markdown using Marker (optional dependency).

    If marker-pdf is not installed or fails, returns an empty string so the
    existing pypdf fallback can take over.
    """
    if not MARKER_ENABLED:
        return ""
    try:
        from marker.converters.pdf import PdfConverter
        from marker.models import create_model_dict
        from marker.output import text_from_rendered

        converter = PdfConverter(artifact_dict=create_model_dict())
        rendered = converter(file_path)
        text, _, _ = text_from_rendered(rendered)
        return text or ""
    except ImportError:
        logger.info("marker-pdf not installed; skipping Marker PDF parser")
        return ""
    except Exception as exc:
        logger.warning("Marker PDF parse failed for %s: %s", file_path, exc)
        return ""


def _parse_pdf_local(file_path: str) -> str:
    try:
        from pypdf import PdfReader
        reader = PdfReader(file_path)
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as exc:
        logger.error("Local PDF parse failed for %s: %s", file_path, exc)
        return ""


def _parse_docx_local(file_path: str) -> str:
    try:
        import docx
        doc = docx.Document(file_path)
        return "\n".join(p.text for p in doc.paragraphs)
    except Exception as exc:
        logger.error("Local DOCX parse failed for %s: %s", file_path, exc)
        return ""


def chunk_text(text: str, max_chars: int = 800, overlap: int = 100) -> List[str]:
    """Simple semantic-ish chunking by paragraphs with overlap."""
    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    chunks: List[str] = []
    current = ""

    for para in paragraphs:
        if len(current) + len(para) > max_chars and current:
            chunks.append(current.strip())
            # overlap from the end of the previous chunk
            if overlap and len(current) > overlap:
                current = current[-overlap:] + "\n" + para
            else:
                current = para
        else:
            current = (current + "\n" + para).strip()

    if current.strip():
        chunks.append(current.strip())

    if not chunks and text.strip():
        # fallback: hard split
        return [text[i:i + max_chars] for i in range(0, len(text), max_chars)]
    return chunks


def _model_from_backend(backend: str) -> str:
    """Map a zvec backend identifier to its canonical model name."""
    mapping = {
        "model2vec": "minishlab/potion-base-8M",
        "sentence_transformers": "all-MiniLM-L6-v2",
        "tfidf_fallback": "tfidf_fallback",
    }
    return mapping.get(backend, backend)


FALLBACK_EMBED_DIM = int(os.getenv("FALLBACK_EMBED_DIM", "256"))
FALLBACK_EMBED_PROVIDER = os.getenv("FALLBACK_EMBED_PROVIDER", "hash_fallback")

# Default ONNX sentence embedder. We use BAAI/bge-small-en-v1.5 because it is
# the smallest supported fastembed model (~67 MB ONNX weights, 384 dims,
# CPU-only via ONNX Runtime). This keeps the base install lightweight while
# still providing real semantic embeddings for RAG.
ONNX_EMBED_MODEL = "BAAI/bge-small-en-v1.5"
_onnx_embedder_instance: Optional[Any] = None


def _hash_embedding(text: str, dim: int = FALLBACK_EMBED_DIM) -> List[float]:
    """Deterministic hash-based embedding used only when no real backend works.

    The resulting vectors are not semantically meaningful. They are a last-resort
    placeholder so the upload pipeline can persist chunks; the deployed router
    will detect a non-matching provider and mark RAG unavailable rather than
    silently returning nonsense retrieval results.
    """
    h = hashlib.sha256(text.encode("utf-8")).digest()
    seed = int.from_bytes(h[:8], "big")
    rng = __import__("random").Random(seed)
    return [rng.uniform(-1.0, 1.0) for _ in range(dim)]


def _fallback_embeddings(chunks: List[str]) -> Tuple[List[List[float]], Dict[str, Any]]:
    """Produce deterministic, non-semantic fallback embeddings and metadata.

    This path is reached only when both the engine's zvec block and the default
    fastembed ONNX backend fail. It lets the upload complete, but RAG is
    intentionally disabled in the deployed runtime because the provider is not
    a semantic embedder.
    """
    dim = FALLBACK_EMBED_DIM
    embeddings = [_hash_embedding(chunk, dim) for chunk in chunks]
    meta = {
        "provider": FALLBACK_EMBED_PROVIDER,
        "backend": FALLBACK_EMBED_PROVIDER,
        "dimensions": dim,
        "model": FALLBACK_EMBED_PROVIDER,
    }
    return embeddings, meta


def _onnx_embed_model() -> Any:
    """Lazily load the default fastembed ONNX sentence embedder as a singleton."""
    global _onnx_embedder_instance
    if _onnx_embedder_instance is None:
        from fastembed import TextEmbedding

        logger.info("Loading ONNX embedding model %s ...", ONNX_EMBED_MODEL)
        _onnx_embedder_instance = TextEmbedding(model_name=ONNX_EMBED_MODEL)
    return _onnx_embedder_instance


def _embed_with_onnx(chunks: List[str]) -> Tuple[List[List[float]], Dict[str, Any]]:
    """Embed *chunks* using the default fastembed ONNX model.

    Returns real-valued dense vectors plus metadata that matches the stanza
    expected by the deployed router's embedding validation.
    """
    model = _onnx_embed_model()
    raw_embeddings = list(model.embed(chunks))
    embeddings: List[List[float]] = []
    for vec in raw_embeddings:
        # fastembed may return numpy arrays or sequences.
        arr = vec.tolist() if hasattr(vec, "tolist") else list(vec)
        embeddings.append([float(v) for v in arr])

    if not embeddings or len(embeddings) != len(chunks):
        raise ValueError(
            f"ONNX embedder returned {len(embeddings)} vectors for {len(chunks)} chunks"
        )

    meta = {
        "provider": "fastembed",
        "backend": "fastembed",
        "model": ONNX_EMBED_MODEL,
        "dimensions": len(embeddings[0]),
    }
    return embeddings, meta


async def embed_chunks(chunks: List[str]) -> Tuple[List[List[float]], Optional[Dict[str, Any]]]:
    """Get embeddings for chunks using the Cerebrum zvec block, falling back to ONNX.

    Priority:
      1. Engine zvec block (when available).
      2. Default fastembed ONNX sentence embedder (bare-install fallback).
      3. Raise ValueError so the caller can use the non-semantic hash fallback.

    Returns the embeddings plus metadata describing the embedding space so the
    same model family can be used at query time.
    """
    if not chunks:
        return [], None

    try:
        result = await _execute_block("zvec", {}, {"operation": "batch_embed", "texts": chunks})
        data = result.get("result", {})
        embeddings = data.get("embeddings", data.get("result", []))
        if not embeddings or len(embeddings) != len(chunks):
            raise ValueError(
                f"zvec returned {len(embeddings)} embeddings for {len(chunks)} chunks"
            )

        backend = data.get("backend", "unknown")
        dimensions = data.get("dimensions", len(embeddings[0]) if embeddings else 0)
        model = data.get("model") or _model_from_backend(backend)
        meta = {
            "provider": "zvec",
            "backend": backend,
            "dimensions": dimensions,
            "model": model,
        }
        return embeddings, meta
    except Exception as exc:
        logger.warning("zvec embedding failed: %s; trying ONNX fallback", exc)

    try:
        embeddings, meta = _embed_with_onnx(chunks)
        logger.info("ONNX fallback embedded %d chunks with model %s", len(embeddings), meta["model"])
        return embeddings, meta
    except Exception as exc:
        logger.error("ONNX fallback embedding failed: %s", exc)
        raise ValueError(f"Embedding backends unavailable: {exc}") from exc


async def process_upload(session_id: str, file_paths: List[str]):
    """Background pipeline: parse, chunk, embed and persist indexed data.

    Chunks are stored in a persistent ChromaDB collection per session. If ChromaDB
    fails, the pipeline falls back to an in-memory store so the user can still
    proceed (with a warning).
    """
    state = get_session(session_id)
    if not state:
        logger.error("process_upload called for unknown session %s", session_id)
        return

    # Each upload is a fresh attempt; clear any previous degradation so a
    # successful re-upload can persist to ChromaDB.
    state.index_status = None

    state.upload.status = "processing"
    state.upload.progress = 0.05
    state.upload.message = "Parsing documents..."
    update_session(session_id, state)

    failed: List[str] = []
    all_chunks: List[str] = []
    source_files: List[str] = []
    source_hashes: List[str] = []

    total = len(file_paths)
    for idx, path in enumerate(file_paths):
        file_name = Path(path).name
        file_hash = _stable_file_hash(path)
        try:
            text = await parse_document(path)
            if text.strip():
                chunks = chunk_text(text)
                all_chunks.extend(chunks)
                source_files.extend([file_name] * len(chunks))
                source_hashes.extend([file_hash] * len(chunks))
            else:
                failed.append(file_name)
        except Exception as exc:
            logger.exception("Failed to parse %s: %s", path, exc)
            failed.append(file_name)
        state.upload.progress = 0.1 + 0.3 * ((idx + 1) / total)
        update_session(session_id, state)

    state.corpus = "\n\n".join(all_chunks)
    state.chunks = all_chunks
    state.upload.total_chunks = len(all_chunks)
    state.upload.progress = 0.45
    state.upload.message = "Chunking text..."
    update_session(session_id, state)

    embeddings: List[List[float]] = []
    embedding_meta: Optional[Dict[str, Any]] = None
    if all_chunks:
        state.upload.progress = 0.6
        state.upload.message = f"Embedding {len(all_chunks)} chunks..."
        update_session(session_id, state)
        try:
            embeddings, embedding_meta = await embed_chunks(all_chunks)
            state.embeddings = embeddings
            state.embedding_meta = embedding_meta
        except ValueError as exc:
            logger.warning(
                "Embedding backends failed for session %s: %s; using non-semantic hash fallback",
                session_id,
                exc,
            )
            embeddings, embedding_meta = _fallback_embeddings(all_chunks)
            state.embeddings = embeddings
            state.embedding_meta = embedding_meta
            state.index_status = "degraded"
            state.upload.message = (
                f"RAG unavailable: embedding backend failed ({exc}). "
                "Chunks are stored with non-semantic placeholders; install the embedding extras to restore RAG."
            )

    state.upload.progress = 0.85
    if state.index_status != "degraded":
        state.upload.message = state.upload.message or "Persisting vector index..."
    update_session(session_id, state)

    chroma_ok = store_chunks(
        session_id,
        all_chunks,
        embeddings,
        source_files=source_files,
        source_hashes=source_hashes,
        embedding_meta=embedding_meta,
    )
    if chroma_ok:
        state.upload.indexed_collection = collection_name(session_id)
        if state.index_status is None:
            state.upload.message = f"Indexed {len(all_chunks)} chunks"
    else:
        logger.warning("ChromaDB persistence failed for session %s; falling back to in-memory store", session_id)
        state.upload.indexed_collection = None
        state.upload.message = f"Indexed {len(all_chunks)} chunks (in-memory fallback; ChromaDB unavailable)"

    state.upload.progress = 1.0
    if failed or state.index_status == "degraded" or not chroma_ok:
        state.upload.status = "completed_with_warnings"
    else:
        state.upload.status = "completed"
    state.upload.failed_files = failed
    state.phase = 3
    state.updated_at = __import__("datetime").datetime.utcnow()
    update_session(session_id, state)

