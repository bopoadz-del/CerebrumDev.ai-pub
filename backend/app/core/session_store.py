import logging
from typing import Dict, Optional

from ..models.session import SessionState, UploadResult
from .session_persistence import load_session_state, save_session_state

logger = logging.getLogger(__name__)
_session_store: Dict[str, SessionState] = {}


def create_session(session_id: str, user_id: str) -> SessionState:
    """Create a session, recording ownership BEFORE any content can exist.

    Ownership is written first and its failure is fatal. It used to be written
    after the session and its failure swallowed with a warning, on the reasoning
    that the index "must never break sessions". That reasoning is inverted: the
    ownership row is the only thing that ties a session to an account, so a
    session created without one is invisible to every per-account query --
    including the erasure path. It cannot be listed, cannot be exported, and
    cannot be deleted on request, while still holding chat history and uploaded
    documents. Under GDPR that is an unerasable record created by a swallowed
    exception.

    Failing loudly costs one session. Failing quietly costs a permanent orphan.
    """
    if user_id and user_id != "anonymous":
        from .accounts_store import record_session_owner

        # Deliberately unguarded: if ownership cannot be recorded, no session.
        record_session_owner(session_id, user_id)

    state = SessionState(session_id=session_id, user_id=user_id)
    _session_store[session_id] = state
    save_session_state(state)
    return state


def _rehydrate_from_chroma(session_id: str) -> Optional[SessionState]:
    """Rebuild a session from its persisted ChromaDB collection if it exists."""
    try:
        from .chroma_store import load_session_upload
        data = load_session_upload(session_id)
    except Exception as exc:
        logger.warning("Could not query ChromaDB for session %s: %s", session_id, exc)
        return None

    if not data:
        return None

    owner = None
    try:
        from .accounts_store import session_owner

        owner = session_owner(session_id)
    except Exception as exc:  # noqa: BLE001 — ownership lookup must not block recovery
        logger.warning("Could not resolve session owner for %s: %s", session_id, exc)

    state = SessionState(
        session_id=session_id,
        user_id=owner or "anonymous",
        phase=3,
        phase_status="in_progress",
    )
    state.chunks = data.get("chunks", [])
    state.embeddings = data.get("embeddings", [])
    state.embedding_meta = data.get("embedding_meta")
    if state.embedding_meta is None:
        state.index_status = "degraded"
    state.upload = UploadResult(
        status="completed",
        progress=1.0,
        total_chunks=data.get("total_chunks", 0),
        indexed_collection=data.get("indexed_collection"),
        message=f"Rehydrated {data.get('total_chunks', 0)} chunks from persistent index",
    )
    _session_store[session_id] = state
    save_session_state(state)
    logger.info("Rehydrated session %s from ChromaDB collection %s", session_id, state.upload.indexed_collection)
    return state


def get_session(session_id: str) -> Optional[SessionState]:
    """Return session state from memory, disk snapshot, or ChromaDB."""
    state = _session_store.get(session_id)
    if state is not None:
        return state

    # Try the persisted JSON snapshot first.
    state = load_session_state(session_id)
    if state is not None:
        _session_store[session_id] = state
        logger.info("Restored session %s from disk snapshot", session_id)
        return state

    # Snapshot missing; try to rebuild from vector index alone.
    return _rehydrate_from_chroma(session_id)


def update_session(session_id: str, state: SessionState) -> SessionState:
    _session_store[session_id] = state
    save_session_state(state)
    return state
