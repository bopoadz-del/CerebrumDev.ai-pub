import json

import pytest

from app.core import session_store
from app.core.session_persistence import _backup_path, _state_path


@pytest.fixture(autouse=True)
def _clear_sessions(monkeypatch, tmp_path):
    """Use a temporary storage directory and clear the in-memory store."""
    monkeypatch.setattr(session_store, "_session_store", {})
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    # Re-import session_persistence picks up STORAGE_PATH from env at import
    # time, so patch its constant directly as well.
    import app.core.session_persistence as sp
    import app.core.chroma_store as chroma_store
    monkeypatch.setattr(sp, "STORAGE_PATH", str(tmp_path / "storage"))
    # Keep tests from writing into ./storage/chroma or sharing the global client.
    monkeypatch.setattr(chroma_store, "CHROMA_PERSIST_DIR", str(tmp_path / "chroma"))
    chroma_store._chroma_client = None
    yield


@pytest.mark.asyncio
async def test_create_and_get_session():
    state = session_store.create_session("sess_create", "u1")
    assert state.session_id == "sess_create"
    assert state.user_id == "u1"

    # Simulate restart: drop in-memory store and reload.
    session_store._session_store.clear()
    restored = session_store.get_session("sess_create")
    assert restored is not None
    assert restored.session_id == "sess_create"
    assert restored.user_id == "u1"


@pytest.mark.asyncio
async def test_update_session_persists():
    state = session_store.create_session("sess_update", "u2")
    state.phase = 3
    state.phase_status = "completed"
    session_store.update_session("sess_update", state)

    session_store._session_store.clear()
    restored = session_store.get_session("sess_update")
    assert restored.phase == 3
    assert restored.phase_status == "completed"


@pytest.mark.asyncio
async def test_embedding_meta_persists_in_snapshot():
    state = session_store.create_session("sess_embedding_meta", "u2")
    state.embedding_meta = {
        "backend": "model2vec",
        "dimensions": 256,
        "model": "minishlab/potion-base-8M",
    }
    session_store.update_session("sess_embedding_meta", state)

    # Verify the snapshot contains the metadata.
    snapshot_path = _state_path("sess_embedding_meta")
    assert snapshot_path.exists()
    text = snapshot_path.read_text(encoding="utf-8")
    assert '"embedding_meta"' in text

    session_store._session_store.clear()
    restored = session_store.get_session("sess_embedding_meta")
    assert restored.embedding_meta == state.embedding_meta


@pytest.mark.asyncio
async def test_large_fields_persisted_in_snapshot():
    state = session_store.create_session("sess_large", "u3")
    state.chat_history = [{"role": "user", "content": "hello"}]
    session_store.update_session("sess_large", state)

    # Snapshot should contain chat_history.
    snapshot_path = _state_path("sess_large")
    assert snapshot_path.exists()
    text = snapshot_path.read_text(encoding="utf-8")
    assert "chat_history" in text

    # Restored state should preserve the persisted values.
    session_store._session_store.clear()
    restored = session_store.get_session("sess_large")
    assert restored.chat_history == state.chat_history


@pytest.mark.asyncio
async def test_atomic_write_creates_backup():
    state = session_store.create_session("sess_atomic", "u4")
    session_store.update_session("sess_atomic", state)

    original = _state_path("sess_atomic").read_text(encoding="utf-8")
    state.phase = 4
    session_store.update_session("sess_atomic", state)

    backup = _backup_path("sess_atomic")
    assert backup.exists()
    assert backup.read_text(encoding="utf-8") == original


@pytest.mark.asyncio
async def test_corrupt_snapshot_falls_back_to_backup():
    state = session_store.create_session("sess_corrupt", "u5")
    session_store.update_session("sess_corrupt", state)

    # Corrupt the main file but leave the backup intact.
    _state_path("sess_corrupt").write_text("not json", encoding="utf-8")

    session_store._session_store.clear()
    restored = session_store.get_session("sess_corrupt")
    assert restored is not None
    assert restored.session_id == "sess_corrupt"


@pytest.mark.asyncio
async def test_unknown_version_is_skipped():
    state = session_store.create_session("sess_version", "u6")
    session_store.update_session("sess_version", state)

    # Remove the backup so the corrupt main file is the only candidate.
    backup = _backup_path("sess_version")
    if backup.exists():
        backup.unlink()

    path = _state_path("sess_version")
    data = path.read_text(encoding="utf-8")
    data = data.replace('"version": 2', '"version": 999')
    path.write_text(data, encoding="utf-8")

    session_store._session_store.clear()
    restored = session_store.get_session("sess_version")
    assert restored is None


@pytest.mark.asyncio
async def test_chat_history_restored_after_restart():
    state = session_store.create_session("sess_restore", "u7")
    state.chat_history = [
        {"role": "user", "content": f"message {i}"} for i in range(5)
    ]
    session_store.update_session("sess_restore", state)

    session_store._session_store.clear()
    restored = session_store.get_session("sess_restore")
    assert restored is not None
    assert restored.chat_history == state.chat_history


@pytest.mark.asyncio
async def test_chat_history_capped_to_max_persisted_messages(monkeypatch):
    import app.core.session_persistence as sp

    monkeypatch.setattr(sp, "MAX_PERSISTED_CHAT_MESSAGES", 5)
    state = session_store.create_session("sess_cap", "u8")
    state.chat_history = [
        {"role": "user", "content": f"message {i}"} for i in range(10)
    ]
    session_store.update_session("sess_cap", state)

    snapshot = json.loads(_state_path("sess_cap").read_text(encoding="utf-8"))
    assert len(snapshot["chat_history"]) == 5
    assert snapshot["chat_history"][0]["content"] == "message 5"
    assert snapshot["chat_history"][-1]["content"] == "message 9"

    session_store._session_store.clear()
    restored = session_store.get_session("sess_cap")
    assert restored is not None
    assert len(restored.chat_history) == 5
    assert restored.chat_history[-1]["content"] == "message 9"


@pytest.mark.asyncio
async def test_version_1_snapshot_loads_with_defaults():
    """A version-1 snapshot missing chat_history loads successfully."""
    session_dir = _state_path("sess_v1").parent
    session_dir.mkdir(parents=True, exist_ok=True)
    version_1 = {
        "version": 1,
        "session_id": "sess_v1",
        "user_id": "u9",
        "phase": 3,
        "phase_status": "in_progress",
    }
    _state_path("sess_v1").write_text(
        json.dumps(version_1, indent=2), encoding="utf-8"
    )

    session_store._session_store.clear()
    restored = session_store.get_session("sess_v1")
    assert restored is not None
    assert restored.session_id == "sess_v1"
    assert restored.user_id == "u9"
    assert restored.chat_history == []
