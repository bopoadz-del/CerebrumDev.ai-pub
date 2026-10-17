"""The Factory-stamped /health refuses a STORAGE_PATH that is not a directory.

Live 2026-10-08 (release cycle on 0e50fcc1, smoke build plt_d13d147a852b433b):
TESTER failed the writer's own security test
``test_health_fails_closed_when_storage_path_is_poisoned`` -- STORAGE_PATH set
to a regular FILE must read 503 not_ready. The writer was right; the module
under test is the one the Factory stamps (deploy.render_health), whose disk
check asked only exists() and os.access(R|W): a writable file passes both, so
/health answered 200 on a disk the service cannot keep data on. The writer
cannot fix a stamped module, so the rework round failed the same way.
"""

from __future__ import annotations

import types

from app.factory.build.deploy import HEALTH_CHECK_NAMES, render_health


def _stamped_health(monkeypatch):
    revision = types.ModuleType("app.revision")
    revision.current_app_revision = lambda: "test"
    revision.current_app_mark = lambda: "test"
    monkeypatch.setitem(__import__("sys").modules, "app.revision", revision)
    module = types.ModuleType("stamped_health")
    exec(compile(render_health(), "app/health.py", "exec"), module.__dict__)
    return module


def _disk(body):
    return {c["name"]: c for c in body["checks"]}[HEALTH_CHECK_NAMES[1]]


def test_a_file_where_the_storage_root_should_be_is_not_ready(monkeypatch, tmp_path):
    module = _stamped_health(monkeypatch)
    poisoned = tmp_path / "not-a-directory"
    poisoned.write_text("a file where the storage root should be", encoding="utf-8")
    monkeypatch.setenv("STORAGE_PATH", str(poisoned))

    code, body = module.evaluate_health()

    assert code == 503
    assert body["ok"] is False
    assert _disk(body)["ok"] is False


def test_a_directory_storage_root_still_counts_as_the_disk(monkeypatch, tmp_path):
    module = _stamped_health(monkeypatch)
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))

    _code, body = module.evaluate_health()

    assert _disk(body)["ok"] is True
