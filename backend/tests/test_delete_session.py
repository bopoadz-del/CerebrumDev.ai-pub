"""Owner: "add an option to delete session from the panel"."""

from __future__ import annotations

from app.core import accounts_store, data_rights, session_store


def _make(session_id: str, account: str):
    state = session_store.create_session(session_id, account)
    state.chat_history = [{"role": "user", "content": "a bakery platform"}]
    session_store.update_session(session_id, state)
    return state


def test_a_deleted_session_is_gone_from_the_owners_list_and_the_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    _make("sess_del_aaaaaaaaaaaa", "acct_del_1")
    _make("sess_keep_bbbbbbbbbbb", "acct_del_1")

    report = data_rights.purge_session("sess_del_aaaaaaaaaaaa")

    assert report["ok"] is True, report
    assert accounts_store.sessions_for_owner("acct_del_1") == ["sess_keep_bbbbbbbbbbb"]
    assert "sess_del_aaaaaaaaaaaa" not in session_store._session_store
    assert session_store.get_session("sess_del_aaaaaaaaaaaa") is None, "must not be restorable"


def test_the_generated_platform_workspace_goes_with_it(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    monkeypatch.setenv("FACTORY_OUTPUTS_ROOT", str(tmp_path / "factory_outputs"))
    _make("sess_ws_cccccccccccc", "acct_del_2")
    ws = tmp_path / "factory_outputs" / "sessions" / "sess_ws_cccccccccccc" / "bakery"
    ws.mkdir(parents=True)
    (ws / "app.py").write_text("x", encoding="utf-8")

    assert data_rights.purge_session("sess_ws_cccccccccccc")["ok"] is True
    assert not (tmp_path / "factory_outputs" / "sessions" / "sess_ws_cccccccccccc").exists()


def test_an_id_that_is_not_a_path_segment_touches_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    (tmp_path / "sessions").mkdir()
    (tmp_path / "canary.txt").write_text("alive", encoding="utf-8")

    report = data_rights.purge_session("../sessions")

    assert report["ok"] is False
    assert (tmp_path / "canary.txt").read_text(encoding="utf-8") == "alive"
    assert (tmp_path / "sessions").is_dir()


def test_the_route_refuses_someone_elses_session(client):
    res = client.delete("/v1/sessions/sess_not_mine_000000", headers={"Authorization": "Bearer nope"})
    assert res.status_code in (401, 403, 404)
