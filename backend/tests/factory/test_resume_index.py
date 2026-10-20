"""Item 2 PR-3: the cross-session resume index maps a full input hash to the
workspace that built it, so a later session with the same inputs can reuse it.
The index only POINTS; the resume is still guarded by reattach_point +
assert_resumable (not exercised here). These unit tests cover the pure index:
store/lookup, misses, a vanished workspace, and a torn entry.
"""
import app.factory.build.resume_index as ri


def _redirect(monkeypatch, tmp_path):
    monkeypatch.setattr("app.factory.paths.factory_outputs_root", lambda: tmp_path)


def test_record_and_lookup_round_trip(tmp_path, monkeypatch):
    _redirect(monkeypatch, tmp_path)
    workspace = tmp_path / "sessions" / "s1" / "prod"
    workspace.mkdir(parents=True)
    assert ri.lookup_resume("hashA") is None  # miss before record
    assert ri.record_resume("hashA", workspace) is True
    got = ri.lookup_resume("hashA")
    assert got is not None and got == workspace


def test_lookup_miss_for_other_hash(tmp_path, monkeypatch):
    _redirect(monkeypatch, tmp_path)
    w = tmp_path / "w"
    w.mkdir()
    ri.record_resume("hashA", w)
    assert ri.lookup_resume("hashB") is None


def test_vanished_workspace_is_a_miss(tmp_path, monkeypatch):
    _redirect(monkeypatch, tmp_path)
    gone = tmp_path / "sessions" / "old" / "prod"
    gone.mkdir(parents=True)
    ri.record_resume("hashA", gone)
    import shutil

    shutil.rmtree(gone)
    # the recorded dir no longer exists -> lookup must not point at it
    assert ri.lookup_resume("hashA") is None


def test_torn_entry_is_a_miss(tmp_path, monkeypatch):
    _redirect(monkeypatch, tmp_path)
    d = tmp_path / "resume_index"
    d.mkdir(parents=True)
    (d / "hashA.json").write_text("{not json", encoding="utf-8")
    assert ri.lookup_resume("hashA") is None


def test_empty_hash_is_safe(tmp_path, monkeypatch):
    _redirect(monkeypatch, tmp_path)
    assert ri.record_resume("", tmp_path) is False
    assert ri.lookup_resume("") is None


def test_resume_hook_imports_resolve():
    """Guard the exact import paths the generate_approved_product resume hook
    uses (a wrong module path there is a swallowed ImportError that silently
    disables resume). This mirror fails if any path drifts."""
    from app.factory.blocks_lock import resolve_lock
    from app.factory.build.blueprint_cache import (
        factory_code_version,
        stage_resume_on,
    )
    from app.factory.build.resume_index import lookup_resume, record_resume
    from app.factory.build.runner import full_input_hash
    from app.factory.build_jobs import reattach_point

    for fn in (
        resolve_lock,
        factory_code_version,
        stage_resume_on,
        lookup_resume,
        record_resume,
        full_input_hash,
        reattach_point,
    ):
        assert callable(fn)
