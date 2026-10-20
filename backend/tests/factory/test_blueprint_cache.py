"""Item 2 keystone: brief-keyed blueprint cache. An unchanged brief (same
vertical, same Factory code version) reuses the cached blueprint instead of
re-drafting; any of those changing is a different key and re-drafts. The cache
dir is redirected to tmp so these need no real outputs root; the module is
pure, so no LLM/draft is exercised.

Fail-first: before the change the cache did not exist and every draft was a
fresh (non-deterministic) architect call.
"""
import app.factory.build.blueprint_cache as bc


def _redirect(monkeypatch, tmp_path):
    monkeypatch.setattr("app.factory.paths.factory_outputs_root", lambda: tmp_path)


def test_flag_default_on_and_off_switch(monkeypatch):
    monkeypatch.delenv("FACTORY_STAGE_RESUME", raising=False)
    assert bc.stage_resume_on() is True  # ON by default (option B)
    for off in ("0", "off", "false", "NO"):
        assert bc.stage_resume_on({"FACTORY_STAGE_RESUME": off}) is False
    for on in ("on", "1", "yes", ""):
        assert bc.stage_resume_on({"FACTORY_STAGE_RESUME": on}) is True


def test_factory_code_version_reads_env():
    assert bc.factory_code_version({"GIT_COMMIT": "abc123"}) == "abc123"
    assert bc.factory_code_version({"RENDER_GIT_COMMIT": "r1"}) == "r1"
    assert bc.factory_code_version({}) == ""


def test_key_stable_and_moves():
    k = bc.brief_blueprint_key("a brief", vertical="retail", factory_sha="sha1")
    assert k == bc.brief_blueprint_key("a brief", vertical="retail", factory_sha="sha1")
    # whitespace around the brief is normalized
    assert k == bc.brief_blueprint_key("  a brief \n", vertical="retail", factory_sha="sha1")
    assert k != bc.brief_blueprint_key("other", vertical="retail", factory_sha="sha1")
    assert k != bc.brief_blueprint_key("a brief", vertical="finance", factory_sha="sha1")
    # a new Factory deploy invalidates the cached blueprint
    assert k != bc.brief_blueprint_key("a brief", vertical="retail", factory_sha="sha2")


def test_store_load_round_trip(tmp_path, monkeypatch):
    _redirect(monkeypatch, tmp_path)
    key = bc.brief_blueprint_key("b", vertical="", factory_sha="s")
    assert bc.load_blueprint(key) is None  # miss before store
    blueprint = {"capabilities": [{"capability_id": "work_orders"}], "country": "QA"}
    assert bc.store_blueprint(key, blueprint) is True
    got = bc.load_blueprint(key)
    assert got == blueprint
    # a different key is still a miss
    assert bc.load_blueprint(bc.brief_blueprint_key("other", "", "s")) is None


def test_torn_entry_is_a_miss(tmp_path, monkeypatch):
    _redirect(monkeypatch, tmp_path)
    key = bc.brief_blueprint_key("b", "", "s")
    d = tmp_path / "blueprint_cache"
    d.mkdir(parents=True)
    (d / f"{key}.json").write_text("{not valid json", encoding="utf-8")
    assert bc.load_blueprint(key) is None
